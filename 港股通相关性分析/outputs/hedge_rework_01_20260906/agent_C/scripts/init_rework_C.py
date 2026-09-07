#!/usr/bin/env python3
"""Initialize the isolated C-group R1 work ledger without fake execution times."""
import csv
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
OUT = ROOT / 'outputs/hedge_rework_01_20260906/agent_C'
CONTROL = ROOT / 'outputs/hedge_rework_01_20260906/control'
ASSIGNMENTS = CONTROL / 'assignments.json'

TASK_COLUMNS = [
    'task_id', 'owner', 'fund_id', 'phase', 'status', 'started_at_utc',
    'finished_at_utc', 'action', 'inputs', 'outputs', 'detailed_result',
    'validation', 'blocker_type', 'blocker_evidence', 'next_action', 'run_command',
]

PHASES = {
    'P1': '逐只官方范围与产品通道核验',
    'P2': 'PCF获取解析与日期/数量核对',
    'P3': 'PCF证券事件与停复牌核验',
    'P4': '对冲工具历史行情与报价质量',
    'P5': '篮子面板、训练、内层选择与端点残差',
    'P6': '新期/探索、相关性区间、稳健性与成本',
    'P7': '逐只独立检查、结论与交付',
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    assignments = json.loads(ASSIGNMENTS.read_text(encoding='utf-8'))['C']
    now = datetime.now(timezone.utc).isoformat()
    input_hash = sha(ASSIGNMENTS)
    tasks = [{
        'task_id': 'C-R0-INIT', 'owner': 'C', 'fund_id': 'GLOBAL', 'phase': 'P0',
        'status': 'DONE', 'started_at_utc': now, 'finished_at_utc': now,
        'action': '创建C组独占返工目录、任务台账和永久脚本目录',
        'inputs': {'assignments': str(ASSIGNMENTS), 'assignments_sha256': input_hash},
        'outputs': {'directory': str(OUT), 'scripts': str(OUT / 'scripts')},
        'detailed_result': 'R0初始化实际完成；逐基金研究阶段仍保持TODO，未批量填入执行时间。',
        'validation': f'{len(assignments)}个C组标的已载入，分母hash={input_hash}',
        'blocker_type': 'NONE', 'blocker_evidence': None,
        'next_action': '先执行includeExpired修复与520760代表基金取数链',
        'run_command': 'python3 agent_C/scripts/init_rework_C.py',
    }]
    for item in assignments:
        fid = item['fund_id']
        for phase, action in PHASES.items():
            tasks.append({
                'task_id': f'C-{fid}-{phase}', 'owner': 'C', 'fund_id': fid, 'phase': phase,
                'status': 'TODO', 'started_at_utc': None, 'finished_at_utc': None,
                'action': action, 'inputs': {'assignment_id': fid, 'assignment_hash': input_hash},
                'outputs': {}, 'detailed_result': '已初始化；等待实际执行。', 'validation': None,
                'blocker_type': 'NONE', 'blocker_evidence': None,
                'next_action': '按阶段开始时写入真实时间、完成时写入真实结果',
                'run_command': None,
            })
    write_json(OUT / 'tasks.json', tasks)
    with (OUT / 'tasks.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=TASK_COLUMNS)
        w.writeheader()
        for row in tasks:
            out = dict(row)
            for key in ('inputs', 'outputs'):
                out[key] = json.dumps(out[key], ensure_ascii=False) if out[key] else '[]'
            w.writerow(out)
    meta = {
        'created_at_utc': now, 'owner': 'C', 'fund_count': len(assignments),
        'phase_task_count': len(assignments) * len(PHASES), 'r0_task_count': 1,
        'tasks_path': str(OUT / 'tasks.json'), 'assignments_sha256': input_hash,
        'python': platform.python_version(), 'platform': platform.platform(),
        'repair_status': 'RUNNING', 'research_status': 'PARTIAL',
    }
    write_json(OUT / 'R0_INITIALIZATION.json', meta)
    (OUT / 'STATUS.md').write_text(
        f'# C组返工状态\n\n更新时间：{now}\n\n'
        f'- 分配分母：{len(assignments)}只；任务阶段记录：{len(tasks)}（P0=1，P1-P7逐只TODO）。\n'
        '- repair_status：RUNNING；research_status：PARTIAL。\n'
        '- 返工口径：用户要求的期货/香港ETF优先、OOS Pearson rho≥0.60；旧VR门槛不作为新合格硬门槛。\n'
        '- 下一步：修复STK历史请求参数，保存03069/03174/HBI真实K线，并以520760开始端到端研究。\n',
        encoding='utf-8')
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
