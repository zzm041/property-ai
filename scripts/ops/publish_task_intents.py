"""Supervise this bounded publisher alongside Worker after approved0012 rollout.

Test mode requires an explicit JSON array of new intent IDs; no old-order scan.
Production environment provisioning/release remains separately approved.
"""
import argparse
import json
from pathlib import Path

def main():
    from app.core.config import get_settings
    from app.core.database import create_database_engine
    from app.worker.task_intents import run_publisher
    p=argparse.ArgumentParser();p.add_argument('--intent-ids-file',type=Path);p.add_argument('--seconds',type=int,default=900)
    a=p.parse_args();s=get_settings()
    if s.app_env=='test' and not a.intent_ids_file: p.error('test requires exact new intent IDs')
    def ids():
        if a.intent_ids_file is None:return None
        value=json.loads(a.intent_ids_file.read_text(encoding='utf-8'))
        if not isinstance(value,list) or not all(type(i) is int and i>0 for i in value):raise ValueError('Invalid intent IDs')
        return tuple(value)
    engine=create_database_engine(s.active_database_url)
    try:run_publisher(engine,s,ids_supplier=ids,seconds=a.seconds)
    finally:engine.dispose()

if __name__=='__main__':main()
