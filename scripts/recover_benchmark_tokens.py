"""Explicit recovery of a stopped benchmark task; preserve the original config."""
from __future__ import annotations
import argparse, asyncio
from dataclasses import replace
from datetime import datetime, timezone
from app.benchmark import BENCH, read, write, sha
from app.config import get_settings
from app.pipeline import Pipeline
from app.storage import Store
from scripts.run_benchmark import RecordingClient, export_sample

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('task_id');parser.add_argument('--max-tokens',type=int,default=128000)
    parser.add_argument('--credential-alias',choices=['primary','alternate'],default='primary')
    parser.add_argument('--gateway-ip')
    args=parser.parse_args()
    if not 64000<args.max_tokens<=128000:raise ValueError('恢复预算必须高于64000且不超过128000')
    settings=replace(get_settings(),data_dir=BENCH/'runs/artifacts',model_timeout_seconds=1800)
    if args.credential_alias=='alternate':
        if not settings.edumind_alt_key:raise ValueError('未配置备用凭据')
        settings=replace(settings,codex_key=settings.edumind_alt_key)
    if args.gateway_ip:
        import socket,ipaddress
        from urllib.parse import urlparse
        address=str(ipaddress.ip_address(args.gateway_ip));host=urlparse(settings.codex_base_url).hostname;original=socket.getaddrinfo
        def resolve(name,*a,**kw):return original(address if name in (host,host.encode()) else name,*a,**kw)
        socket.getaddrinfo=resolve
    store=Store(settings);task=store.get(args.task_id)
    if not task or task['status']!='error':raise ValueError('只能恢复已停止且error的任务，不可干扰运行中任务')
    if 'max_completion_tokens' not in task['message']:raise ValueError('本命令仅修复输出预算耗尽，不自动改变其他错误的实验配置')
    config=store.config(task['id']);meta=config.get('benchmark',{})
    if meta.get('batch')!='hard-v1':raise ValueError('仅限本次综合题实验')
    folder=store.root/task['id'];stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    config={**config,'generator_max_tokens':args.max_tokens,'reviewer_max_tokens':args.max_tokens,
            'recovery':{'reason':task['message'],'previous_config':task['config_name'],'max_tokens':args.max_tokens,
                        'model_timeout_seconds':1800,'changed_prompts':False,'time_utc':stamp}}
    name=f'{stamp}_recovery_config.json';write(folder/name,config)
    with store._db() as db:db.execute('UPDATE tasks SET config_name=? WHERE id=?',(name,task['id']))
    store.log_event(task['id'],'recovery',task['current_round'],'预算耗尽后显式恢复：128000 token、1800秒；原配置和失败费用保留')
    await Pipeline(settings,store,RecordingClient(settings,folder,args.credential_alias)).run(task['id'])
    task=store.get(task['id']);q=next(q for q in read(BENCH/'manifest.json')['questions'] if q['id']==meta['question_id'])
    sid=await asyncio.to_thread(export_sample,store,task,q,meta['workflow'],meta['batch'])
    print(task['status'],sid,task['message'],flush=True)

if __name__=='__main__':asyncio.run(main())
