"""Command-line interface for {@ service_name @}.

Subcommands:
    train      — Train a new model from CSV data
    evaluate   — Evaluate a trained model on labeled data
    predict    — Batch predictions on new data

Usage:
    python -m src.{@ service_slug @}.cli train --config configs/config.yaml --input data/raw/train.csv
    python -m src.{@ service_slug @}.cli evaluate \
        --config configs/config.yaml --input data/raw/test.csv --model models/model.joblib
    python -m src.{@ service_slug @}.cli predict \
        --input data/new.csv --output results/predictions.csv --model models/model.joblib

TODO: Rename imports from src.{@ service_slug @} to your actual service package name.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

import pandas as pd

from .config import ServiceConfig
from .evaluation import ModelEvaluator
from .prediction import ServicePredictor
from .training.train import Trainer

logger = logging.getLogger(__name__)


def setup_logging(log_file: str = "service.log", level: int = logging.INFO) -> None:
    """Configure logging for CLI with both file and console handlers."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )


def train_command(args: argparse.Namespace) -> int:
    """Execute train subcommand.

    Returns 0 on success, 1 on failure.
    """
    try:
        # The config is USED, not merely validated. This line read
        # `ServiceConfig.from_yaml(args.config)` and threw the result away —
        # its own comment said "Validate config exists and is parseable" — so
        # the `mlflow` block an adopter filled in never reached MLflow and
        # every run went to MLflow's own default.
        config = ServiceConfig.from_yaml(args.config)
        trainer = Trainer(
            data_path=args.input,
            output_dir=args.model_dir,
            mlflow_config=config.mlflow,
        )
        result = trainer.run(optuna_trials=args.optuna_trials)

        # Save metrics
        if args.metrics_output:
            import json

            metrics_path = Path(args.metrics_output)
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            with open(metrics_path, "w") as f:
                json.dump(result["metrics"], f, indent=2)
            logger.info("Metrics saved to %s", metrics_path)

        logger.info(
            "Training completed — quality gates: %s", "PASSED" if result["quality_gates"]["all_passed"] else "FAILED"
        )
        return 0
    except Exception as e:
        logger.error("Training failed: %s", e, exc_info=True)
        return 1


def evaluate_command(args: argparse.Namespace) -> int:
    """Execute evaluate subcommand."""
    try:
        evaluator = ModelEvaluator.from_files(args.model, args.preprocessor)
        config = ServiceConfig.from_yaml(args.config)

        data = pd.read_csv(args.input)
        logger.info("Loaded %d samples for evaluation", len(data))

        y = data[config.data.target_column]
        X = data.drop(columns=[config.data.target_column])

        evaluator.evaluate(X, y, output_path=args.output)

        # Optional fairness metrics
        if args.fairness_features:
            fairness_features = args.fairness_features.split(",")
            fairness_metrics = evaluator.compute_fairness_metrics(X, y, fairness_features)
            if args.output:
                import json

                fairness_path = Path(args.output).parent / "fairness_metrics.json"
                with open(fairness_path, "w") as f:
                    json.dump(fairness_metrics, f, indent=2)
                logger.info("Fairness metrics saved to %s", fairness_path)

        logger.info("Evaluation completed successfully")
        return 0
    except Exception as e:
        logger.error("Evaluation failed: %s", e, exc_info=True)
        return 1


def predict_command(args: argparse.Namespace) -> int:
    """Execute predict subcommand."""
    try:
        predictor = ServicePredictor.from_files(args.model, args.preprocessor)
        predictions = predictor.predict_batch(
            input_path=args.input,
            output_path=args.output,
            include_proba=not args.no_proba,
            threshold=args.threshold,
        )
        logger.info("Predictions saved: %d rows", len(predictions))
        return 0
    except Exception as e:
        logger.error("Prediction failed: %s", e, exc_info=True)
        return 1


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser with train/evaluate/predict subcommands."""
    parser = argparse.ArgumentParser(
        description="{@ service_name @} — ML prediction system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--seed", type=int, help="Random seed for reproducibility")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- train ---
    train_p = subparsers.add_parser("train", help="Train a new model")
    train_p.add_argument("--config", required=True, help="Path to config YAML")
    train_p.add_argument("--input", required=True, help="Path to training CSV")
    train_p.add_argument("--model-dir", default="models", help="Directory to save model")
    train_p.add_argument("--metrics-output", help="Path to save metrics JSON")
    train_p.add_argument("--optuna-trials", type=int, default=50, help="Optuna trials")

    # --- evaluate ---
    eval_p = subparsers.add_parser("evaluate", help="Evaluate a trained model")
    eval_p.add_argument("--config", required=True, help="Path to config YAML")
    eval_p.add_argument("--input", required=True, help="Path to labeled CSV")
    eval_p.add_argument("--model", required=True, help="Path to trained model")
    eval_p.add_argument("--preprocessor", default=None, help="Path to preprocessor")
    eval_p.add_argument("--output", help="Path to save evaluation JSON")
    eval_p.add_argument("--fairness-features", help="Comma-separated sensitive features")

    # --- predict ---
    pred_p = subparsers.add_parser("predict", help="Batch predictions")
    pred_p.add_argument("--input", required=True, help="Path to input CSV")
    pred_p.add_argument("--output", required=True, help="Path to save predictions")
    pred_p.add_argument("--model", required=True, help="Path to trained model")
    pred_p.add_argument("--preprocessor", default=None, help="Path to preprocessor")
    pred_p.add_argument("--threshold", type=float, default=0.5, help="Classification threshold")
    pred_p.add_argument("--no-proba", action="store_true", help="Exclude probabilities")

    return parser


def cli_main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)

    log_level = getattr(logging, args.log_level.upper())
    setup_logging(level=log_level)

    # Set global seed if provided
    if args.seed is not None:
        try:
            from common_utils.seed import set_seed

            set_seed(args.seed)
            logger.info("Random seed set to %d", args.seed)
        except ImportError:
            logger.warning("common_utils.seed not available — skipping")

    commands = {
        "train": train_command,
        "evaluate": evaluate_command,
        "predict": predict_command,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args)
    else:
        logger.error("Unknown command: %s", args.command)
        return 1


if __name__ == "__main__":
    sys.exit(cli_main())
