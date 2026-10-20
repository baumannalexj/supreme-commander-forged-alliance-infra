param([Parameter(ValueFromRemainingArguments)][string[]]$Args)
uv run --project $PSScriptRoot python -m mergequeue @Args
