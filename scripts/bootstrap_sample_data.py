from datetime import date
from pathlib import Path

from alphapilot.data.mock import MockMarketDataProvider


def main() -> None:
    output_dir = Path("data/processed/sample")
    output_dir.mkdir(parents=True, exist_ok=True)
    provider = MockMarketDataProvider()
    for symbol in ["600000", "000001", "000333", "600519"]:
        frame = provider.get_daily_bars(symbol, date(2024, 1, 1), date.today())
        path = output_dir / f"{symbol}.csv"
        frame.to_csv(path, index=False)
        print(path)


if __name__ == "__main__":
    main()
