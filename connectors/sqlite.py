import sqlite3

from configurations.settings import SQLITE

db = sqlite3.connect(SQLITE)