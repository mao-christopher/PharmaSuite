"""MongoDB configuration; the application never falls back to local inventory files."""
import os
from pymongo import MongoClient
from pymongo.write_concern import WriteConcern


def create_client():
    return MongoClient(
        os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"),
        serverSelectionTimeoutMS=3000, connectTimeoutMS=3000, socketTimeoutMS=5000,
        appname="pharma-inventory", tz_aware=True,
    )


def get_database(client=None):
    return (client if client is not None else create_client())[os.getenv("MONGO_DB_NAME", "pharma")]


def get_collection(name, database=None):
    db = database if database is not None else get_database()
    return db.get_collection(name, write_concern=WriteConcern(w="majority", j=True))


def init_indexes(database):
    # _id is MongoDB's unique pharmacy identity; revision provides compare-and-swap.
    database.command("ping")
