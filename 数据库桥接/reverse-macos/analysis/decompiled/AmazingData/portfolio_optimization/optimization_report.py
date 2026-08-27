# Source Generated with Decompyle++
# File: optimization_report.pyc (Python 3.12)

__doc__ = "\n组合优化 HTML 可视化报告生成器\n\n参考 ad_factor_analysis 暗色科技主题风格，使用 ECharts 5.5 生成交互式图表。\n包含：\n    - 因子概要（因子收益率时序、统计指标）\n    - 风险分解（饼图：共同因子 vs 特质风险）\n    - 四种优化目标对比（柱状图 + 表格）\n    - 个股权重分布（柱状图 + 详细表格）\n    - 协方差矩阵可视化（热力图）\n\n使用方式:\n    from optimization_report import OptimizationReportRenderer\n    renderer = OptimizationReportRenderer(results_dict)\n    renderer.save('ma10_report.html')\n"
import os
import json
import numpy as np
import pandas as pd
from datetime import datetime
ECHARTS_DARK_THEME = {
    'color': [
        '#00d4ff',
        '#7b68ee',
        '#ff6b9d',
        '#00e676',
        '#ffab40',
        '#40c4ff',
        '#b388ff',
        '#ff80ab',
        '#69f0ae',
        '#ffd740'],
    'backgroundColor': 'transparent',
    'textStyle': {
        'color': '#b0bec5' },
    'title': {
        'textStyle': {
            'color': '#e0e6ed' } },
    'line': {
        'itemStyle': {
            'borderWidth': 1 },
        'lineStyle': {
            'width': 1.5 },
        'symbolSize': 4,
        'symbol': 'circle' },
    'categoryAxis': {
        'axisLine': {
            'lineStyle': {
                'color': '#37474f' } },
        'axisLabel': {
            'color': '#78909c' },
        'splitLine': {
            'lineStyle': {
                'color': [
                    '#263238'] } } },
    'valueAxis': {
        'axisLine': {
            'lineStyle': {
                'color': '#37474f' } },
        'axisLabel': {
            'color': '#78909c' },
        'splitLine': {
            'lineStyle': {
                'color': [
                    '#263238'] } } },
    'tooltip': {
        'backgroundColor': 'rgba(10,14,23,0.95)',
        'borderColor': '#1e2d3d',
        'textStyle': {
            'color': '#b0bec5' } },
    'dataZoom': {
        'textStyle': {
            'color': '#78909c' },
        'dataBackground': {
            'lineStyle': {
                'color': '#37474f' },
            'areaStyle': {
                'color': 'rgba(0,212,255,0.08)' } },
        'selectedDataBackground': {
            'lineStyle': {
                'color': '#00d4ff' },
            'areaStyle': {
                'color': 'rgba(0,212,255,0.2)' } },
        'handleStyle': {
            'color': '#37474f' } } }
CSS = "\n:root {\n    --bg: #0a0e17;\n    --bg-card: #111827;\n    --bg-card-hover: #1a2332;\n    --border: #1e2d3d;\n    --text: #b0bec5;\n    --text-bright: #e0e6ed;\n    --text-dim: #607d8b;\n    --accent: #00d4ff;\n    --accent2: #7b68ee;\n    --accent3: #ff6b9d;\n    --green: #00e676;\n    --orange: #ffab40;\n    --red: #ff5252;\n    --radius: 10px;\n}\n\n* { margin: 0; padding: 0; box-sizing: border-box; }\n\nbody {\n    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',\n                 'Microsoft YaHei', sans-serif;\n    background: var(--bg);\n    color: var(--text);\n    line-height: 1.6;\n    min-height: 100vh;\n}\n\n/* ===== Header ===== */\n.header {\n    background: linear-gradient(135deg, #0a1628 0%, #0d1f3c 40%, #0a1628 100%);\n    padding: 48px 24px 36px;\n    text-align: center;\n    position: relative;\n    overflow: hidden;\n}\n.header::before {\n    content: '';\n    position: absolute;\n    top: -120px;\n    right: -80px;\n    width: 400px;\n    height: 400px;\n    background: radial-gradient(circle, rgba(0,212,255,0.06) 0%, transparent 70%);\n    border-radius: 50%;\n}\n.header h1 {\n    font-size: 26px;\n    font-weight: 700;\n    color: var(--text-bright);\n    position: relative;\n}\n.header h1 span {\n    color: var(--accent);\n}\n.header .meta {\n    margin-top: 12px;\n    font-size: 13px;\n    color: var(--text-dim);\n    display: flex;\n    justify-content: center;\n    gap: 24px;\n    flex-wrap: wrap;\n    position: relative;\n}\n\n/* ===== Navigation ===== */\n.nav {\n    position: sticky;\n    top: 0;\n    z-index: 100;\n    display: flex;\n    justify-content: center;\n    flex-wrap: wrap;\n    gap: 4px;\n    padding: 0 20px;\n    background: rgba(17, 24, 39, 0.95);\n    backdrop-filter: blur(12px);\n    border-bottom: 1px solid var(--border);\n}\n.nav a {\n    color: var(--text-dim);\n    text-decoration: none;\n    padding: 14px 20px;\n    font-size: 13px;\n    font-weight: 500;\n    transition: all .25s;\n    border-bottom: 2px solid transparent;\n    cursor: pointer;\n}\n.nav a:hover { color: var(--text-bright); background: rgba(255,255,255,0.02); }\n.nav a.active { color: var(--accent); border-bottom-color: var(--accent); }\n\n/* ===== Content ===== */\n.content {\n    max-width: 1300px;\n    margin: 0 auto;\n    padding: 32px 20px 60px;\n}\n\n/* ===== Section ===== */\n.section {\n    margin-bottom: 48px;\n}\n.section h2 {\n    font-size: 18px;\n    font-weight: 700;\n    color: var(--text-bright);\n    margin-bottom: 20px;\n    display: flex;\n    align-items: center;\n    gap: 8px;\n}\n.section h2 .icon {\n    font-size: 20px;\n}\n\n/* ===== Card ===== */\n.card {\n    background: var(--bg-card);\n    border: 1px solid var(--border);\n    border-radius: var(--radius);\n    padding: 24px;\n    margin-bottom: 16px;\n    transition: border-color .3s;\n}\n.card:hover { border-color: #2a3a50; }\n.card-title {\n    font-size: 14px;\n    font-weight: 600;\n    color: var(--text-bright);\n    margin-bottom: 16px;\n}\n\n/* ===== Grid layouts ===== */\n.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }\n.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }\n\n/* ===== Metric cards ===== */\n.metric-grid {\n    display: grid;\n    grid-template-columns: repeat(4, 1fr);\n    gap: 12px;\n}\n.metric {\n    background: rgba(255,255,255,0.02);\n    border: 1px solid var(--border);\n    border-radius: 8px;\n    padding: 14px;\n    text-align: center;\n    transition: border-color .3s;\n}\n.metric:hover { border-color: var(--accent); }\n.metric .label {\n    font-size: 11px;\n    color: var(--text-dim);\n    margin-bottom: 6px;\n    text-transform: uppercase;\n    letter-spacing: 0.5px;\n}\n.metric .value {\n    font-size: 18px;\n    font-weight: 700;\n    color: var(--text-bright);\n}\n.metric .value.accent { color: var(--accent); }\n.metric .value.green { color: var(--green); }\n.metric .value.orange { color: var(--orange); }\n.metric .value.red { color: var(--red); }\n\n/* ===== Table ===== */\ntable {\n    width: 100%;\n    border-collapse: collapse;\n    font-size: 13px;\n}\ncaption {\n    font-weight: 600;\n    font-size: 14px;\n    color: var(--text-bright);\n    margin-bottom: 12px;\n    text-align: left;\n}\nth, td {\n    padding: 10px 14px;\n    border-bottom: 1px solid var(--border);\n    text-align: right;\n}\nth:first-child, td:first-child { text-align: left; }\nth {\n    background: rgba(0,212,255,0.06);\n    color: var(--accent);\n    text-transform: uppercase;\n    font-size: 11px;\n    letter-spacing: 0.5px;\n}\ntr:hover td { background: rgba(0,212,255,0.03); }\n\n/* ===== Chart container ===== */\n.chart {\n    width: 100%;\n    height: 420px;\n}\n\n/* ===== Badge ===== */\n.badge {\n    display: inline-block;\n    padding: 4px 12px;\n    border-radius: 12px;\n    font-size: 12px;\n    font-weight: 600;\n}\n.badge.positive { background: rgba(0,230,118,0.15); color: var(--green); }\n.badge.negative { background: rgba(255,82,82,0.15); color: var(--red); }\n\n/* ===== Footer ===== */\n.footer {\n    text-align: center;\n    padding: 24px;\n    font-size: 12px;\n    color: var(--text-dim);\n    border-top: 1px solid var(--border);\n}\n\n/* ===== Responsive ===== */\n@media (max-width: 900px) {\n    .grid-2, .grid-3 { grid-template-columns: 1fr; }\n    .metric-grid { grid-template-columns: repeat(2, 1fr); }\n    .header { padding: 32px 16px 24px; }\n    .header h1 { font-size: 20px; }\n    .header .meta { flex-direction: column; gap: 4px; }\n    .content { padding: 24px 12px 40px; }\n    .card { padding: 16px; }\n    .chart { height: 300px; }\n    table { font-size: 11px; }\n    th, td { padding: 8px 10px; }\n}\n@media (max-width: 480px) {\n    .header h1 { font-size: 17px; }\n    .nav a { padding: 10px 14px; font-size: 11px; }\n    .chart { height: 250px; }\n    .metric .value { font-size: 15px; }\n}\n"

class OptimizationReportRenderer:
    '''
    组合优化 HTML 报告生成器

    :param results: 从 ma10_portfolio_optimization.py main() 返回的结果字典
    '''
    
    def __init__(self = None, results = None):
        self.results = results
        self._charts_js = []
        self._chart_counter = 0

    
    def _next_chart_id(self = None):
        return f'''chart_{self._chart_counter}'''

    
    def _fmt_num(self, v, decimals = (4,)):
        '''安全格式化数字'''
        pass
    # WARNING: Decompyle incomplete

    
    def _fmt_pct(self, v, decimals = (2,)):
        '''安全格式化百分比'''
        pass
    # WARNING: Decompyle incomplete

    
    def _badge(self, value, threshold = (0,)):
        '''根据正负返回徽章 HTML'''
        pass
    # WARNING: Decompyle incomplete

    
    def _render_chart(self = None, option = None, chart_id = None, height = (420,)):
        '''将 ECharts option 渲染为 HTML + JS'''
        
        def _json_default(obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if None(obj, (np.floating,)):
                if np.isnan(obj):
                    return None
                return float(obj)
            if None(obj, np.ndarray):
                return obj.tolist()
            if None(obj, pd.Timestamp):
                return obj.strftime('%Y-%m-%d')
            return None(obj)

        opt_json = json.dumps(option, ensure_ascii = False, default = _json_default)
        opt_json = opt_json.replace('"___FMT_PCT___"', 'function(v){ return v.toFixed(2)+"%"; }')
        opt_json = opt_json.replace('"___FMT_NUM4___"', 'function(v){ return v.toFixed(4); }')
        opt_json = opt_json.replace('"___FMT_NUM2___"', 'function(v){ return v.toFixed(2); }')
        opt_json = opt_json.replace('"___NEWLINE___"', '"{b}\\n{d}%"'.replace('\\n', '\n'))
        init_func_name = f'''_init_{chart_id}'''
        self._charts_js.append(f'''\n    window.{init_func_name} = function() {{\n        var el = document.getElementById(\'{chart_id}\');\n        if (!el || !window.echarts) return;\n        var chart = echarts.init(el, \'dark\');\n        chart.setOption({opt_json});\n    }};\n''')
        return f'''<div class="chart" id="{chart_id}" style="height:{height}px;"></div>'''

    
    def _build_factor_summary_section(self = None):
        '''因子概要 section'''
        factor_return = self.results.get('factor_return')
        processed_factor = self.results.get('processed_factor')
    # WARNING: Decompyle incomplete

    
    def _build_covariance_section(self = None):
        '''协方差矩阵可视化 section'''
        cov_adjuster = self.results.get('cov_adjuster')
    # WARNING: Decompyle incomplete

    
    def _build_specific_risk_section(self = None):
        '''特质风险 section'''
        spec_adjuster = self.results.get('spec_adjuster')
    # WARNING: Decompyle incomplete

    
    def _build_optimization_section(self = None):
        '''四种优化目标对比 section'''
        w_utility = self.results.get('weights_utility')
        w_ir = self.results.get('weights_ir')
        w_neutral = self.results.get('weights_neutral')
        w_minrisk = self.results.get('weights_minrisk')
    # WARNING: Decompyle incomplete

    
    def _build_risk_decomposition_section(self = None):
        '''风险分解 section — 使用最大化风险调整后收益的权重'''
        w_utility = self.results.get('weights_utility')
        cov_adjuster = self.results.get('cov_adjuster')
        spec_adjuster = self.results.get('spec_adjuster')
        processed_factor = self.results.get('processed_factor')
        summary = self.results.get('summary_utility', { })
    # WARNING: Decompyle incomplete

    
    def _build_factor_return_section(self = None):
        '''因子收益率求解结果 section'''
        factor_return = self.results.get('factor_return')
    # WARNING: Decompyle incomplete

    
    def build_html(self = None, title = None):
        '''构建完整 HTML 报告'''
        gen_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sections = [
            ('factor-summary', '⚡', '因子概要', self._build_factor_summary_section()),
            ('factor-return', '📊', '因子收益', self._build_factor_return_section()),
            ('covariance', '📦', '协方差矩阵', self._build_covariance_section()),
            ('specific-risk', '🔬', '特质风险', self._build_specific_risk_section()),
            ('optimization', '🎯', '组合优化', self._build_optimization_section()),
            ('risk-decomp', '👁', '风险分解', self._build_risk_decomposition_section())]
        nav_items = ''
        section_html = ''
        for sec_id, sec_icon, sec_name, sec_content in sections:
            if not sec_content.strip():
                continue
            nav_items += f'''            <a href="#{sec_id}">{sec_icon} {sec_name}</a>\n'''
            section_html += f'''\n        <div class="section" id="{sec_id}">\n            <h2><span class="icon">{sec_icon}</span> {sec_name}</h2>\n            {sec_content}\n        </div>'''
        charts_init_js = '\n'.join(self._charts_js)
        html = f'''<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n    <meta charset="UTF-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n    <title>{title}</title>\n    <!-- 主 CDN: jsdelivr，失败后自动切换备用 CDN -->\n    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"\n            onerror="(function(){{var s=document.createElement(\'script\');s.src=\'https://cdn.bootcdn.net/ajax/libs/echarts/5.5.0/echarts.min.js\';s.onerror=function(){{var s2=document.createElement(\'script\');s2.src=\'https://unpkg.com/echarts@5.5.0/dist/echarts.min.js\';document.head.appendChild(s2);}};document.head.appendChild(s);}})()">\n    </script>\n    <style>{CSS}</style>\n</head>\n<body>\n    <div class="header">\n        <h1><span>&#9670;</span> {title} <span>&#9670;</span></h1>\n        <div class="meta">\n            <span>生成时间: {gen_time}</span>\n            <span>引擎: ECharts 5.5</span>\n        </div>\n    </div>\n\n    <nav class="nav">\n{nav_items}    </nav>\n\n    <div class="content">\n{section_html}    </div>\n\n    <div class="footer">\n        &copy; {datetime.now().year} AmazingData · Portfolio Optimization Report · Powered by ECharts\n    </div>\n\n    <script>\n        // 注册暗色主题\n        echarts.registerTheme(\'dark\', {json.dumps(ECHARTS_DARK_THEME, ensure_ascii = False)});\n\n        // 权重表格全局转发函数（HTML onclick 通过 table_id 调用）\n        window.renderWeightTable = function(tableId) {{\n            var fn = window[\'renderWeightTable_\' + tableId];\n            if (fn) fn();\n        }};\n        window.changeWeightPage = function(tableId, delta) {{\n            var fn = window[\'changeWeightPage_\' + tableId];\n            if (fn) fn(delta);\n        }};\n\n{charts_init_js}\n\n        // 批量初始化所有图表和表格（最多重试30次，约6秒）\n        (function initAllCharts(retryCount) {{\n            retryCount = retryCount || 0;\n            if (!window.echarts) {{\n                if (retryCount < 30) {{\n                    setTimeout(function(){{ initAllCharts(retryCount + 1); }}, 200);\n                }} else {{\n                    console.error(\'ECharts 加载失败（已重试30次），图表将无法显示。请检查网络连接。\');\n                    // 显示加载失败提示\n                    document.querySelectorAll(\'.chart\').forEach(function(el) {{\n                        el.innerHTML = \'<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#ff5252;font-size:14px;">ECharts 加载失败，请刷新页面或检查网络连接</div>\';\n                    }});\n                    // 权重表格即使没有 echarts 也要渲染\n                    for (var key in window) {{\n                        if (key.startsWith(\'_init_weight_table_\')) {{\n                            try {{ window[key](); }} catch(e) {{}}\n                        }}\n                    }}\n                }}\n                return;\n            }}\n            for (var key in window) {{\n                if (key.startsWith(\'_init_chart_\') || key.startsWith(\'_init_weight_table_\')) {{\n                    try {{ window[key](); }} catch(e) {{ console.warn(\'Init failed:\', key, e); }}\n                }}\n            }}\n        }})();\n\n        // 导航高亮\n        document.querySelectorAll(\'.nav a\').forEach(function(a) {{\n            a.addEventListener(\'click\', function() {{\n                document.querySelectorAll(\'.nav a\').forEach(function(x) {{ x.classList.remove(\'active\'); }});\n                this.classList.add(\'active\');\n            }});\n        }});\n\n        // 响应窗口大小变化\n        window.addEventListener(\'resize\', function() {{\n            document.querySelectorAll(\'.chart\').forEach(function(el) {{\n                var c = echarts.getInstanceByDom(el);\n                if (c) c.resize();\n            }});\n        }});\n\n        // 默认激活第一个导航项\n        (function() {{\n            var first = document.querySelector(\'.nav a\');\n            if (first) first.classList.add(\'active\');\n        }})();\n    </script>\n</body>\n</html>'''
        return html

    
    def save(self = None, filepath = None, title = None):
        '''生成并保存 HTML 报告'''
        html = self.build_html(title = title)
        f = open(filepath, 'w', encoding = 'utf-8')
        f.write(html)
        None(None, None)
        print(f'''  [OK] HTML 报告已保存至: {filepath}''')
        return filepath
        with None:
            if not None:
                pass
        continue


# WARNING: Decompyle incomplete
