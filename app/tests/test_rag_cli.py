from __future__ import annotations

import pytest

from app.rag_cli import build_parser


@pytest.mark.parametrize(
    "argv",
    [
        ["--config", "custom.yaml", "doctor", "--deep"],
        ["doctor", "--config", "custom.yaml", "--deep"],
    ],
)
def test_common_config_is_accepted_before_or_after_subcommand(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)

    assert args.command == "doctor"
    assert args.config == "custom.yaml"
    assert args.deep is True


def test_subcommand_common_flags_only_override_when_explicit() -> None:
    args = build_parser().parse_args(
        ["--config", "custom.yaml", "--verbose", "doctor", "--deep"]
    )

    assert args.config == "custom.yaml"
    assert args.verbose is True
    assert args.force is False
    assert args.doc_id is None
    assert args.limit is None
