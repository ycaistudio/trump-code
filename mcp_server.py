#!/usr/bin/env python3
"""
川普密碼 — MCP Server

讓任何支援 MCP 的 AI 工具（Claude Code、Cursor 等）
直接連接川普密碼系統，查信號、看預測、找套利。

安裝到 Claude Code：
  在 ~/.claude/settings.json 加：
  {
    "mcpServers": {
      "trump-code": {
        "command": "python3",
        "args": ["/path/to/trump-code/mcp_server.py"]
      }
    }
  }

然後在 Claude Code 裡就能用：
  「川普密碼今天的信號是什麼？」
  「模型排行榜」
  「有沒有套利機會？」
  「系統健康嗎？」
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).parent
DATA = BASE / "data"


# =====================================================================
# 數據讀取（跟 trump_code_cli.py 共用邏輯）
# =====================================================================

def _load(filename: str) -> dict | list | None:
    path = DATA / filename
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def get_signals() -> dict:
    """今日信號"""
    report = _load('daily_report.json') or {}
    ai = _load('opus_analysis.json') or {}
    breaker = _load('circuit_breaker_state.json') or {}

    return {
        'date': report.get('date', '?'),
        'signals': report.get('signals_detected', []),
        'posts_today': report.get('posts_today', 0),
        'system_status': breaker.get('system_status', 'UNKNOWN'),
        'opus_insight': ai.get('missed_signals', {}).get('finding', ''),
    }


def get_models() -> dict:
    """模型排行"""
    briefing = _load('opus_briefing.json') or {}
    ai = _load('opus_analysis.json') or {}

    perf = briefing.get('model_performance', {})
    ranked = sorted(perf.items(), key=lambda x: -x[1].get('win_rate', 0))

    models = []
    for mid, s in ranked:
        models.append({
            'id': mid,
            'name': s.get('name', mid),
            'win_rate': s.get('win_rate', 0),
            'avg_return': s.get('avg_return', 0),
            'total_trades': s.get('total_trades', 0),
        })

    adj = ai.get('models_to_adjust', {})
    return {
        'models': models,
        'boost': [m['model'] for m in adj.get('boost', [])],
        'eliminate': [m['model'] for m in adj.get('eliminate', [])],
    }


def get_prediction() -> dict:
    """今日預測方向"""
    report = _load('daily_report.json') or {}
    direction = report.get('direction_summary', {})
    hit = report.get('historical_hit_rate', {})

    return {
        'consensus': direction.get('consensus', 'NEUTRAL'),
        'long_models': direction.get('LONG', 0),
        'short_models': direction.get('SHORT', 0),
        'hit_rate': hit.get('rate', 0),
        'verified_total': hit.get('verified', 0),
    }


def get_arbitrage() -> dict:
    """預測市場套利機會"""
    pm = _load('prediction_market_scan.json') or {}
    return {
        'date': pm.get('date', '?'),
        'signals': pm.get('signals', []),
        'opportunities': pm.get('opportunities', []),
        'total_scanned': pm.get('total_scanned', 0),
    }


def get_health() -> dict:
    """系統健康度"""
    breaker = _load('circuit_breaker_state.json') or {}
    ai = _load('opus_analysis.json') or {}
    learning = _load('learning_report.json') or {}
    events = _load('event_alerts.json') or []

    checks = breaker.get('checks', {})
    return {
        'system_status': breaker.get('system_status', 'UNKNOWN'),
        'vs_random': checks.get('vs_random', {}).get('message', ''),
        'degradation': checks.get('degradation', {}).get('message', ''),
        'consecutive_errors': checks.get('consecutive', {}).get('consecutive_wrong', 0),
        'pattern_shift': ai.get('pattern_shift_detected', False),
        'pattern_detail': ai.get('pattern_shift_details', ''),
        'active_events': [e.get('name', '?') for e in events[-3:] if isinstance(e, dict)],
        'priority_action': ai.get('priority_action', ''),
        'total_rules': 546,
        'learning_summary': learning.get('adjustments', {}).get('summary', {}),
    }


def get_event_alerts() -> dict:
    """醞釀中的事件"""
    events = _load('event_alerts.json') or []
    recent = events[-5:] if isinstance(events, list) else []
    return {
        'active_alerts': [
            {
                'pattern': e.get('name', '?'),
                'severity': e.get('severity', '?'),
                'direction': e.get('expected_direction', '?'),
                'detail': e.get('detail', ''),
            }
            for e in recent if isinstance(e, dict)
        ],
    }


def get_dual_platform() -> dict:
    """雙平台分析（TS vs X）"""
    windows = _load('ts_to_x_windows.json') or []
    active = [w for w in windows if isinstance(w, dict) and w.get('status') == 'ACTIVE']
    return {
        'active_windows': len(active),
        'key_insight': 'Truth Social 是政策信號源，X 是形象窗口。TS 先發，X 晚 6.2 小時。中國信號 100% 不放 X。',
        'china_rule': '中國相關信號加權 ×1.5（刻意隱藏 = 更真實）',
        'x_repost_rule': '會上 X 的信號降權 ×0.8（大眾已知 = 套利空間小）',
        'window_bias': '6 小時窗口內歷史上漲率 63%',
    }


def get_crowd_insights() -> dict:
    """群眾智慧"""
    insights = _load('crowd_insights.json') or []
    return {
        'total': len(insights),
        'recent': [
            {
                'time': i.get('timestamp', '?')[:16],
                'insight': i.get('ai_extracted', ''),
            }
            for i in insights[-5:] if isinstance(i, dict)
        ],
    }


def get_full_report() -> dict:
    """完整報告（所有資料合併）"""
    return {
        'signals': get_signals(),
        'prediction': get_prediction(),
        'models': get_models(),
        'arbitrage': get_arbitrage(),
        'health': get_health(),
        'events': get_event_alerts(),
        'dual_platform': get_dual_platform(),
        'crowd': get_crowd_insights(),
    }


# =====================================================================
# MCP Protocol（JSON-RPC over stdio）
# =====================================================================

TOOLS = [
    {
        "name": "trump_code_signals",
        "description": "取得川普密碼今日偵測到的信號（TARIFF/DEAL/RELIEF/ACTION/THREAT）和推文數量",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "trump_code_models",
        "description": "取得川普密碼的模型排行榜，包含命中率、報酬率、Opus 的升級/淘汰建議",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "trump_code_predict",
        "description": "取得今日預測方向（BULLISH/BEARISH/NEUTRAL），幾個模型做多、幾個做空",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "trump_code_arbitrage",
        "description": "查詢預測市場（Polymarket/Kalshi）的套利機會",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "trump_code_health",
        "description": "系統健康度檢查：跟隨機比、惡化偵測、連錯停機、模式變化",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "trump_code_events",
        "description": "查看醞釀中的事件：關稅轟炸、RELIEF 轉折、爆量沉默等多日模式",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "trump_code_dual_platform",
        "description": "雙平台分析：Truth Social vs X 的差異、6 小時操作窗口、中國信號加權",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "trump_code_crowd",
        "description": "群眾智慧：用戶透過聊天機器人分享的交易邏輯洞見",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "trump_code_full_report",
        "description": "完整報告：所有數據合併（信號+預測+模型+套利+健康+事件+雙平台+群眾）",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]

TOOL_HANDLERS = {
    "trump_code_signals": get_signals,
    "trump_code_models": get_models,
    "trump_code_predict": get_prediction,
    "trump_code_arbitrage": get_arbitrage,
    "trump_code_health": get_health,
    "trump_code_events": get_event_alerts,
    "trump_code_dual_platform": get_dual_platform,
    "trump_code_crowd": get_crowd_insights,
    "trump_code_full_report": get_full_report,
}


def handle_request(request: dict) -> dict:
    """處理 MCP JSON-RPC 請求。"""
    method = request.get('method', '')
    req_id = request.get('id')

    if method == 'initialize':
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "trump-code",
                    "version": "1.0.0",
                },
            },
        }

    elif method == 'notifications/initialized':
        return None  # 不需要回應

    elif method == 'tools/list':
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    elif method == 'tools/call':
        tool_name = request.get('params', {}).get('name', '')
        handler = TOOL_HANDLERS.get(tool_name)

        if handler:
            try:
                result = handler()
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result, ensure_ascii=False, indent=2),
                            }
                        ],
                    },
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": str(e)},
                }
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }


def main():
    """MCP Server 主循環：讀 stdin JSON-RPC，寫 stdout。"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle_request(request)
        if response:
            sys.stdout.write(json.dumps(response) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    # 如果帶 --test 參數，跑一次測試而不是啟動 server
    if len(sys.argv) > 1 and sys.argv[1] == '--test':
        print("=== Trump Code MCP Server 測試 ===\n")
        for tool in TOOLS:
            name = tool['name']
            handler = TOOL_HANDLERS.get(name)
            if handler:
                try:
                    result = handler()
                    preview = json.dumps(result, ensure_ascii=False)[:200]
                    print(f"✅ {name}")
                    print(f"   {preview}...")
                except Exception as e:
                    print(f"❌ {name}: {e}")
            print()
        print("=== 安裝方式 ===")
        print(f'在 ~/.claude/settings.json 加:')
        print(f'{{"mcpServers": {{"trump-code": {{"command": "python3", "args": ["{Path(__file__).resolve()}"]}}}}}}')
    else:
        main()
