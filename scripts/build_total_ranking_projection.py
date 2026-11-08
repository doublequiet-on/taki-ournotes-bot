"""Build the optional total-ranking score projection from an isolated SQLite copy."""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from ournotes_bot.sources.total_rankings import TotalRankingHistory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='Path to a consistent, isolated copy of the total-ranking database')
    args = parser.parse_args()
    database = args.database.resolve(strict=True)
    if not database.is_file():
        parser.error('database must be a file')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        version, = db.execute('PRAGMA user_version').fetchone()
        source, = db.execute('SELECT source FROM total_identity').fetchone()
    history = TotalRankingHistory(database, source, compressed=version == 2)
    count = history.build_projection()
    print(f'{count} samples -> {history.projection_path}')


if __name__ == '__main__':
    main()
