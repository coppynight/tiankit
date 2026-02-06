import json
import os
import re
from datetime import datetime

# 配置
PROJECT_ROOT = "/Users/xiaokai/clawd/miniexplorer"
PLAN_FILE = f"{PROJECT_ROOT}/docs/plans/ios-app-implementation-ai-plan.md"
OUTPUT_JSON = "/Users/xiaokai/clawd/multi-agent-tool/projects/miniexplorer/status.json"
OUTPUT_HTML = "/Users/xiaokai/clawd/canvas/miniexplorer-dashboard.html"

# 任务定义 (简化版，实际应从 plan.md 解析)
TASKS = [
    {"id": "P5.1", "desc": "ExploreView (探索模式)", "check": "ios/MiniExplorer/Views/ExploreView.swift"},
    {"id": "P5.2", "desc": "CompanionView (陪伴模式)", "check": "ios/MiniExplorer/Views/CompanionView.swift"},
    {"id": "P5.3", "desc": "Components (Avatar/RecordButton)", "check": ["ios/MiniExplorer/Views/Components/AvatarView.swift", "ios/MiniExplorer/Views/Components/RecordButton.swift"]},
    {"id": "P5.4", "desc": "TabView Wiring", "check": "ios/MiniExplorer/Views/ContentView.swift"}, # 假设会改这里
    {"id": "P1.1", "desc": "Project Init", "check": "ios/MiniExplorer.xcodeproj"},
    {"id": "P2.1", "desc": "Coze Bridge", "check": "ios/MiniExplorer/Resources/coze-bridge.html"},
]

def check_file(path):
    full_path = os.path.join(PROJECT_ROOT, path)
    return os.path.exists(full_path)

def scan_status():
    status_data = {
        "project": "MiniExplorer",
        "lastUpdated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tasks": [],
        "phase": "Phase 5",
        "progress": 0
    }
    
    done_count = 0
    total_count = 0
    
    for task in TASKS:
        checks = task["check"]
        if isinstance(checks, str):
            checks = [checks]
            
        all_passed = all(check_file(f) for f in checks)
        state = "done" if all_passed else "pending"
        
        status_data["tasks"].append({
            "id": task["id"],
            "desc": task["desc"],
            "state": state
        })
        
        if task["id"].startswith("P5"):
            total_count += 1
            if state == "done":
                done_count += 1

    if total_count > 0:
        status_data["progress"] = int((done_count / total_count) * 100)
        
    return status_data

def generate_html(data):
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>{data['project']} Dashboard</title>
        <style>
            body {{ font-family: -apple-system, sans-serif; padding: 20px; background: #f5f5f7; }}
            .card {{ background: white; border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }}
            h1 {{ margin: 0 0 10px; font-size: 24px; }}
            .meta {{ color: #888; font-size: 14px; margin-bottom: 20px; }}
            .progress-bar {{ background: #eee; height: 10px; border-radius: 5px; overflow: hidden; }}
            .progress-fill {{ background: #007aff; height: 100%; width: {data['progress']}%; transition: width 0.3s; }}
            .task-list {{ list-style: none; padding: 0; }}
            .task-item {{ padding: 12px 0; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }}
            .task-id {{ font-weight: bold; width: 60px; color: #555; }}
            .task-desc {{ flex: 1; }}
            .status-badge {{ padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }}
            .status-done {{ background: #e5f9e7; color: #2ecc71; }}
            .status-pending {{ background: #fff5e6; color: #f39c12; }}
            .agent-status {{ display: flex; gap: 10px; margin-top: 10px; }}
            .agent {{ background: #f0f0f5; padding: 8px 12px; border-radius: 8px; font-size: 13px; }}
        </style>
        <script>
            setTimeout(() => window.location.reload(), 10000); // Auto refresh
        </script>
    </head>
    <body>
        <div class="card">
            <h1>📱 {data['project']}</h1>
            <div class="meta">Last updated: {data['lastUpdated']}</div>
            
            <h3>Phase 5 Progress: {data['progress']}%</h3>
            <div class="progress-bar">
                <div class="progress-fill"></div>
            </div>
            
            <div class="agent-status">
                <div class="agent">🤖 PM: Active</div>
                <div class="agent">👷 Worker: Standby</div>
                <div class="agent">👀 Reviewer: Standby</div>
            </div>
        </div>

        <div class="card">
            <h3>Tasks</h3>
            <ul class="task-list">
    """
    
    for task in data["tasks"]:
        status_class = f"status-{task['state']}"
        icon = "✅" if task['state'] == "done" else "⏳"
        html += f"""
            <li class="task-item">
                <span class="task-id">{task['id']}</span>
                <span class="task-desc">{task['desc']}</span>
                <span class="status-badge {status_class}">{icon} {task['state'].upper()}</span>
            </li>
        """
        
    html += """
            </ul>
        </div>
    </body>
    </html>
    """
    return html

# 执行
data = scan_status()

# 写入 JSON
os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
with open(OUTPUT_JSON, "w") as f:
    json.dump(data, f, indent=2)

# 写入 HTML
os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
with open(OUTPUT_HTML, "w") as f:
    f.write(generate_html(data))

print(f"Dashboard generated: {OUTPUT_HTML}")
