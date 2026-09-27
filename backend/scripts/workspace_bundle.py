#!/usr/bin/env python3
"""Export or restore app data, recordings, camera setup, inventory and player bookmark."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pharma.db.connection import create_client, get_collection, get_database
from pharma.services.workspace_bundle import export_bundle, restore_bundle


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    export=sub.add_parser('export');export.add_argument('--data-dir',type=Path,required=True)
    export.add_argument('--output',type=Path,required=True);export.add_argument('--api',required=True)
    restore=sub.add_parser('restore');restore.add_argument('--archive',type=Path,required=True)
    restore.add_argument('--data-dir',type=Path,required=True)
    args=p.parse_args();client=create_client()
    try:
        collection=get_collection('pharmacy_state',get_database(client));identity=os.getenv('PHARMACY_ID','default')
        if args.command=='export':
            import httpx
            with httpx.Client(base_url=args.api,timeout=120) as api:
                jobs=api.get('/api/renders');jobs.raise_for_status()
                if jobs.json()['jobs']:raise ValueError('Wait for renders to finish before exporting.')
                recordings=api.get('/api/recordings');recordings.raise_for_status()
                if any(r['status']=='processing' for r in recordings.json()['recordings']):raise ValueError('Wait for video processing to finish.')
                state=api.get('/api/inventory');state.raise_for_status()
                result=export_bundle(args.data_dir,args.output,collection,identity,state.json())
        else:result=restore_bundle(args.archive,args.data_dir,collection,identity)
        print(json.dumps({'schema':result['schema'],'files':len(result['files']),'player':result['player']},indent=2))
    finally:client.close()

if __name__=='__main__':main()
