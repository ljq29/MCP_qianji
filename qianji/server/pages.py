from __future__ import annotations

import json
from html import escape

from qianji.version import __version__

# Single source of truth for the top navigation. Every page renders it through
# render_topbar() so the header markup and active state cannot drift apart.
_NAV_ITEMS = (
    ("console", "/login", "操作台"),
    ("account", "/account", "账号管理"),
    ("about", "/about", "服务说明"),
    ("mcp", "/mcp-config", "添加 MCP"),
    ("docs", "/docs", "API Docs"),
    ("redoc", "/redoc", "ReDoc"),
)


def render_topbar(active: str = "") -> str:
    links = []
    for key, href, label in _NAV_ITEMS:
        cls = ' class="active"' if key == active else ""
        links.append(f'<a{cls} href="{href}">{label}</a>')
    return (
        '<header class="topbar">'
        '<a class="brand" href="/login"><span class="brand-mark">钱</span><span>Qianji Bridge</span></a>'
        f'<nav class="nav" aria-label="主导航">{"".join(links)}</nav>'
        "</header>"
    )


LOGIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<link rel="icon" type="image/svg+xml" href="/static/docs/favicon.svg">
<link rel="stylesheet" href="/static/base.css">
<link rel="stylesheet" href="/static/console/console.css">
</head>
<body data-page="__PAGE__">
__TOPBAR__

<main class="shell">
  <section class="workspace __WORKSPACE_CLASS__" aria-labelledby="workspaceTitle">
    <nav class="subtabs" aria-label="操作台功能">
      <button type="button" class="active" data-subtab="bills">记账</button>
      <button type="button" data-subtab="assets">资产查看</button>
      <button type="button" data-subtab="cards">卡片备份</button>
      <button type="button" data-subtab="categories">分类管理</button>
    </nav>

    <section class="subtab-panel" data-panel="bills" aria-label="记账">
    <div class="workbar">
      <div>
        <p class="eyebrow">Bills</p>
        <h2 id="workspaceTitle">近期流水</h2>
      </div>
      <div class="filters">
        <label>账本 ID
          <input id="queryBookId" type="number" value="-1">
        </label>
        <label>开始日期
          <input id="queryStartDate" type="date">
        </label>
        <label>结束日期
          <input id="queryEndDate" type="date">
        </label>
        <button data-action="bills">按范围拉取</button>
      </div>
    </div>

    <div class="metrics">
      <div class="metric"><span>账单</span><strong id="billCount">0</strong></div>
      <div class="metric"><span>资产</span><strong id="assetCount">0</strong></div>
      <div class="metric"><span>分类</span><strong id="categoryCount">0</strong></div>
      <div class="metric"><span>账本</span><strong id="bookCount">0</strong></div>
    </div>

    <div class="content-grid">
      <section class="list-panel" aria-label="账单列表">
        <div class="section-head">
          <strong>账单列表</strong>
          <span id="billListMeta" class="muted">等待登录</span>
        </div>
        <div id="billList" class="bill-list">
          <div class="empty">未连接钱迹账号。请到「账号管理」页连接账号，连接后这里会自动加载近期账单。</div>
        </div>
        <button class="qj-fab" id="billFab" data-action="newBill" title="记一笔" aria-label="记一笔">＋</button>
      </section>

      <aside class="compose-panel" aria-label="记一笔">
        <div class="section-head">
          <strong>记一笔</strong>
          <span id="composeHint" class="muted">支出</span>
        </div>
        <div class="composer">
          <div class="segmented" role="tablist" aria-label="账单类型">
            <button type="button" class="active" data-bill-type="0">支出</button>
            <button type="button" data-bill-type="1">收入</button>
            <button type="button" data-bill-type="2">转账</button>
          </div>
          <input id="billType" type="hidden" value="0">

          <div class="amount-row">
            <label>金额
              <input id="billMoney" type="number" step="0.01" min="0" placeholder="0.00">
            </label>
            <label>日期
              <input id="billDate" type="date">
            </label>
          </div>

          <label>备注
            <input id="billRemark" placeholder="例如 午餐">
          </label>

          <div class="compose-grid">
            <label id="categoryField">分类
              <select id="billCateId"><option value="0">加载分类后选择</option></select>
            </label>
            <label id="assetField"><span id="assetFieldLabel">支出资产</span>
              <select id="billAssetId"><option value="-1">未指定</option></select>
            </label>
            <label>账本
              <select id="billBookId"><option value="-1">默认账本</option></select>
            </label>
            <label id="targetAssetField" class="is-hidden" hidden><span id="targetAssetFieldLabel">转入资产</span>
              <select id="billTargetAssetId"><option value="-1">未指定</option></select>
            </label>
          </div>

          <label>标签
            <input id="billTags" placeholder="标签名或ID，逗号分隔">
          </label>

          <div class="button-row">
            <button id="createBillButton" data-action="createBill">创建账单</button>
            <button class="secondary" data-action="loadRefs">加载分类/资产/账本/标签</button>
          </div>

          <fieldset id="editBillPanel" class="edit-actions is-hidden" hidden>
            <legend>操作</legend>
            <div class="button-row">
              <button id="saveBillButton" data-action="saveBill">保存修改</button>
              <button id="cancelEditButton" class="secondary" data-action="cancelEdit">取消编辑</button>
              <button id="deleteBillButton" class="danger" data-action="deleteBill">删除账单</button>
            </div>
          </fieldset>
        </div>
      </aside>
    </div>

    <details class="advanced">
      <summary>高级 API 调用</summary>
      <div class="advanced-body">
        <div class="compose-grid">
          <label>Controller
            <input id="genericCtrl" placeholder="asset">
          </label>
          <label>Action
            <input id="genericAct" placeholder="list">
          </label>
        </div>
        <label>Params JSON
          <textarea id="genericParams">{}</textarea>
        </label>
        <div class="button-row">
          <button data-action="genericApi">调用 /api/{ctrl}/{act}</button>
          <button class="secondary" data-action="assets">资产</button>
          <button class="secondary" data-action="categories">分类</button>
          <button class="secondary" data-action="books">账本</button>
          <button class="secondary" data-action="tags">标签</button>
        </div>
        <pre id="apiOutput" class="api-output">Ready.</pre>
      </div>
    </details>
    </section>

    <section class="subtab-panel is-hidden" data-panel="assets" aria-label="资产查看">
      <div class="workbar">
        <div>
          <p class="eyebrow">Assets</p>
          <h2 id="assetViewTitle">资产查看</h2>
        </div>
        <div class="button-row">
          <button class="secondary is-hidden" id="assetBackButton" hidden data-action="backToAssets">← 返回资产列表</button>
          <button class="secondary" id="assetSortButton" data-action="sortAssets">调整顺序</button>
          <button data-action="refreshAssets">刷新资产</button>
        </div>
      </div>
      <div id="assetListView">
        <div class="metrics">
          <div class="metric"><span>资产合计</span><strong id="assetViewTotal">0.00</strong></div>
          <div class="metric"><span>负债合计</span><strong id="assetViewDebt">0.00</strong></div>
          <div class="metric"><span>净资产</span><strong id="assetViewNet">0.00</strong></div>
          <div class="metric"><span>资产账户</span><strong id="assetViewCount">0</strong></div>
        </div>
        <div id="assetList" class="data-list">
          <div class="empty">未连接钱迹账号。请到「账号管理」页连接账号，连接后点击「刷新资产」加载。</div>
        </div>
      </div>
      <div id="assetDetailView" class="is-hidden" hidden>
        <div class="metrics">
          <div class="metric"><span>当前余额</span><strong id="assetDetailMoney">0.00</strong></div>
          <div class="metric"><span>类型</span><strong id="assetDetailType">-</strong></div>
          <div class="metric"><span>币种</span><strong id="assetDetailCurrency">-</strong></div>
          <div class="metric"><span>资产 ID</span><strong id="assetDetailId">-</strong></div>
        </div>
        <div class="split-body">
          <div class="split-list">
            <div class="section-head">
              <strong>交易记录</strong>
              <span id="assetBillsMeta" class="muted">等待加载</span>
            </div>
            <div id="assetBillList" class="bill-list">
              <div class="empty">正在加载交易记录…</div>
            </div>
            <div class="section-head">
              <span class="muted">按时间倒序，点击账单可跳到「记账」页编辑</span>
              <button class="secondary" id="assetBillsMore" data-action="loadMoreAssetBills">加载更多</button>
            </div>
          </div>
          <aside class="split-aside" aria-label="编辑资产">
            <div class="section-head">
              <strong>编辑资产</strong>
              <span id="assetEditHint" class="muted">名称与备注可改</span>
            </div>
            <div class="composer">
              <label>名称
                <input id="assetName" placeholder="资产名称">
              </label>
              <label>备注
                <input id="assetRemark" placeholder="选填">
              </label>
              <div class="button-row">
                <button data-action="saveAsset">保存资产</button>
                <button class="secondary" data-action="reloadAssetDetail">重新载入</button>
              </div>
              <p class="muted" style="margin:0;font-size:12px;">余额与类型不可在此修改；借入/借出资产的名称与备注会通过借贷接口提交。</p>
            </div>
          </aside>
        </div>
      </div>
    </section>

    <section class="subtab-panel is-hidden" data-panel="cards" aria-label="卡片备份">
      <div class="workbar">
        <div>
          <p class="eyebrow">Cards</p>
          <h2>卡片备份</h2>
        </div>
        <div class="button-row">
          <button data-action="refreshCards">刷新卡片</button>
          <button class="secondary" data-action="sortCards">调整顺序</button>
          <button class="secondary" data-action="exportCards">导出备份 (JSON)</button>
        </div>
      </div>
      <div class="split-body">
        <div class="split-list">
          <div class="section-head">
            <strong>卡包列表</strong>
            <span id="cardListMeta" class="muted">等待加载</span>
          </div>
          <div id="cardFilterBar" class="card-filter is-hidden" hidden>
            <input id="cardSearch" type="search" placeholder="搜索银行 / 卡号 / 持卡人 / 备注" autocomplete="off">
            <div id="cardTypeChips" class="chips"></div>
          </div>
          <div id="cardList" class="data-list">
            <div class="empty">未连接钱迹账号。连接后切换到本页会自动加载卡包，可一键导出 JSON 备份。</div>
          </div>
        </div>
        <aside class="split-aside" aria-label="卡片编辑">
          <div class="section-head">
            <strong id="cardFormTitle">新增卡片</strong>
            <span class="muted">点击左侧卡片可复制 / 编辑 / 删除</span>
          </div>
          <div class="composer">
            <label>卡类型
              <select id="cardType">
                <option value="0">普通卡</option>
                <option value="1">信用卡</option>
                <option value="2">银行存折</option>
                <option value="3">证券/债券</option>
                <option value="4">其他</option>
              </select>
            </label>
            <label>卡片名 / 银行
              <input id="cardBank" placeholder="例如 招商银行">
            </label>
            <label>卡号
              <input id="cardNo" autocomplete="off" spellcheck="false" placeholder="例如 6225880212345678">
            </label>
            <label>持卡人
              <input id="cardOwner" placeholder="选填">
            </label>
            <label>备注
              <input id="cardRemark" placeholder="选填，例如 账单日 5 号">
            </label>
            <label>图片 URL
              <input id="cardImages" placeholder="选填，多个用英文逗号分隔">
            </label>
            <input id="cardEditId" type="hidden" value="">
            <div class="button-row">
              <button data-action="saveCard">保存卡片</button>
              <button class="secondary" data-action="resetCardForm">新增卡片</button>
              <button class="secondary" data-action="copyCardJson">复制 JSON</button>
            </div>
            <fieldset id="cardDangerZone" class="edit-actions is-hidden" hidden>
              <legend>危险操作</legend>
              <p class="muted" style="margin:0;font-size:12px;">删除后不可恢复；也可以直接点击左侧卡片选择删除。</p>
              <div class="button-row">
                <button class="danger" data-action="deleteCard">删除卡片</button>
              </div>
            </fieldset>
          </div>
        </aside>
      </div>
    </section>

    <section class="subtab-panel is-hidden" data-panel="categories" aria-label="分类管理">
      <div class="workbar">
        <div>
          <p class="eyebrow">Categories</p>
          <h2>分类管理</h2>
        </div>
        <div class="segmented" role="tablist" aria-label="分类类型">
          <button type="button" class="active" data-cat-type="0">支出</button>
          <button type="button" data-cat-type="1">收入</button>
        </div>
        <div class="segmented" role="tablist" aria-label="查看模式">
          <button type="button" class="active" data-cat-mode="tree">界面</button>
          <button type="button" data-cat-mode="source">源码</button>
        </div>
        <div class="button-row">
          <button data-action="refreshCategories">刷新分类</button>
          <button class="secondary" data-action="sortCategories">调整顺序</button>
          <button class="secondary" data-action="newRootCategory">＋ 新增一级分类</button>
        </div>
      </div>
      <div id="categoryTreeView">
        <div class="section-head">
          <strong>分类树</strong>
          <span id="categoryListMeta" class="muted">等待加载</span>
        </div>
        <div id="categoryList" class="data-list category-tree">
          <div class="empty">未连接钱迹账号。连接后切换到本页会自动加载分类。</div>
        </div>
      </div>
      <div id="categorySourceView" class="is-hidden" hidden>
        <div class="section-head">
          <strong>源码（只读快照，可复制带走）</strong>
          <div class="button-row">
            <div class="segmented segmented-sm" role="tablist" aria-label="源码格式">
              <button type="button" class="active" data-source-format="text">文本</button>
              <button type="button" data-source-format="json">JSON</button>
            </div>
            <button class="secondary" data-action="copyCategorySource">复制</button>
            <button class="secondary" data-action="downloadCategorySource">下载</button>
          </div>
        </div>
        <pre id="categorySourceCode" class="source-code"></pre>
      </div>
    </section>
  </section>

  <section class="login-panel __LOGIN_PANEL_CLASS__" aria-labelledby="loginTitle">
    <div class="welcome">
      <p class="eyebrow">Account</p>
      <h1 id="loginTitle">连接钱迹账号</h1>
      <p class="subtle">使用钱迹账号密码连接当前 API 与 MCP 服务，浏览器可选择仅会话记忆或本地记忆。连接成功后可在「操作台」查看近期流水。</p>
    </div>
    <div class="form">
      <div class="login-fields">
        <label>钱迹账号
          <input id="apiAccount" autocomplete="username" placeholder="邮箱或手机号">
        </label>
        <label>钱迹密码
          <input id="apiPassword" type="password" autocomplete="current-password" placeholder="密码">
        </label>
        <label>本地账号
          <select id="savedAccountSelect"><option value="">选择已记住账号</option></select>
        </label>
        <label class="checkline"><input id="rememberCredentials" type="checkbox">记住到本地浏览器</label>
        <div class="risk-hint span-2">注意：pwd_hash 与密码等价，勾选后将以明文形式保存在此浏览器的 localStorage 中；共享或公共电脑请勿使用。</div>
        <div class="button-row span-2">
          <button data-action="login">连接并加载</button>
          <button class="secondary" data-action="refreshAll">刷新数据</button>
          <button class="secondary" data-action="clearCredentials">清除记忆</button>
        </div>
        <div id="loginStatus" class="status-line span-2"><span class="status-dot"></span><span>未连接</span></div>
      </div>
      <div class="mcp-config" aria-label="MCP 配置">
        <div class="mcp-config-head">
          <div class="mcp-config-title">
            <strong>MCP JSON</strong>
            <span id="mcpConfigHint">填写账号和密码后生成 qj1.md5-b64 Authorization</span>
          </div>
          <button id="copyMcpConfigButton" class="secondary" type="button" data-action="copyMcpConfig">复制</button>
        </div>
        <pre id="mcpConfigJson" class="mcp-config-code">填写钱迹账号和密码后生成 MCP JSON</pre>
      </div>
    </div>
  </section>
</main>

<script>window.__QJ_BOOT__ = {"mcpPath": __MCP_PATH_JSON__};</script>
<script src="/static/console/console.js" defer></script>
</body>
</html>"""


MCP_CONFIG_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>添加 MCP - 钱迹</title>
<link rel="icon" type="image/svg+xml" href="/static/docs/favicon.svg">
<link rel="stylesheet" href="/static/base.css">
<style>
  :root {
    --code-bg: #101828;
    --code-bar: #172033;
    --code-line: #2b364f;
    --code-text: #e7edf6;
  }
  html { background: var(--bg); }
  a { color: inherit; text-decoration: none; }
  .shell {
    width: min(1440px, 100%);
    margin: 0 auto;
    padding: 28px;
    display: grid;
    gap: 20px;
  }
  .panel {
    min-width: 0;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 8px;
    box-shadow: var(--shadow);
    overflow: hidden;
  }
  .panel-head {
    padding: 24px 26px 20px;
    border-bottom: 1px solid var(--line);
    background: linear-gradient(180deg, #ffffff 0%, #f3faf7 100%);
  }
  h1, h2, p { margin-top: 0; }
  h1 { margin-bottom: 10px; font-size: 32px; line-height: 1.16; letter-spacing: 0; }
  h2 { margin: 0; font-size: 18px; line-height: 1.25; letter-spacing: 0; }
  .status {
    margin-top: 14px;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 7px;
    background: var(--surface-2);
    color: var(--muted);
    font-size: 13px;
  }
  .status.ok { border-color: rgba(23, 107, 83, 0.35); background: var(--primary-soft); color: var(--primary-strong); }
  .status.warn { border-color: rgba(161, 92, 19, 0.35); background: #fff7ed; color: var(--warning); }
  .content {
    display: grid;
    grid-template-columns: minmax(240px, 320px) minmax(0, 1fr);
    gap: 20px;
    padding: 24px 26px 26px;
  }
  .mode-list {
    display: grid;
    gap: 8px;
    align-self: start;
  }
  .mode-button {
    width: 100%;
    min-height: 46px;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    border: 1px solid var(--line-strong);
    border-radius: 7px;
    padding: 8px 10px;
    background: #fff;
    color: var(--text);
    font: inherit;
    cursor: pointer;
    text-align: left;
  }
  .mode-button strong { display: block; font-size: 14px; }
  .mode-button span { display: block; color: var(--muted); font-size: 12px; }
  .mode-button .badge {
    min-width: 54px;
    padding: 2px 7px;
    border-radius: 999px;
    background: #eef3f7;
    color: var(--muted);
    font-size: 12px;
    text-align: center;
  }
  .mode-button.active { border-color: var(--primary); background: var(--primary-soft); }
  .mode-button.active .badge { background: var(--primary); color: #fff; }
  .output-grid { display: grid; gap: 16px; min-width: 0; }
  .code-card {
    min-width: 0;
    border: 1px solid var(--code-line);
    border-radius: 8px;
    overflow: hidden;
    background: var(--code-bg);
    box-shadow: 0 12px 28px rgba(15, 23, 42, 0.16);
  }
  .code-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 9px 13px;
    background: var(--code-bar);
    color: #cbd5e1;
    border-bottom: 1px solid var(--code-line);
    font: 600 12px ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    letter-spacing: 0;
  }
  .code-title button {
    min-height: 30px;
    border: 1px solid rgba(167, 243, 208, 0.28);
    border-radius: 7px;
    padding: 0 10px;
    background: rgba(23, 107, 83, 0.28);
    color: #d1fae5;
    font: inherit;
    cursor: pointer;
  }
  pre {
    min-height: 150px;
    max-height: 380px;
    overflow: auto;
    margin: 0;
    padding: 16px;
    color: var(--code-text);
    font: 13px/1.65 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    white-space: pre;
  }
  .url-row {
    display: grid;
    gap: 7px;
    min-width: 0;
    color: var(--muted);
    font-size: 13px;
    font-weight: 650;
  }
  .url-row code {
    min-width: 0;
    overflow: auto;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 7px;
    background: var(--surface-2);
    color: var(--text);
    font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    white-space: nowrap;
  }
  @media (max-width: 820px) {
    .shell { padding: 16px; }
    .content { grid-template-columns: 1fr; padding: 18px; }
  }
</style>
</head>
<body>
__TOPBAR__

<main class="shell">
  <section class="panel">
    <div class="panel-head">
      <p class="eyebrow">MCP Setup</p>
      <h1>添加 MCP</h1>
      <p class="subtle">生成当前服务的 Streamable HTTP MCP 配置和 Agent 自动添加提示词。</p>
      <div class="status __CREDENTIAL_STATUS_CLASS__">__CREDENTIAL_STATUS__</div>
    </div>
    <div class="content">
      <div class="mode-list" role="group" aria-label="MCP mode">
        <button class="mode-button active" type="button" data-mode="default"><span><strong>default</strong><span>不增加 mode，使用服务默认值</span></span><span class="badge">默认</span></button>
        <button class="mode-button" type="button" data-mode="read"><span><strong>read</strong><span>只读查询工具</span></span><span class="badge">14</span></button>
        <button class="mode-button" type="button" data-mode="write"><span><strong>write</strong><span>低噪音核心记账，URL 示例：/mcp?mode=write</span></span><span class="badge">23</span></button>
        <button class="mode-button" type="button" data-mode="advanced"><span><strong>advanced</strong><span>业务写入工具</span></span><span class="badge">39</span></button>
        <button class="mode-button" type="button" data-mode="full"><span><strong>full</strong><span>全部工具和 raw/admin 能力</span></span><span class="badge">all</span></button>
      </div>
      <div class="output-grid">
        <div class="url-row">
          <span>MCP URL</span>
          <code id="mcpUrl">__MCP_URL__</code>
        </div>
        <div class="code-card">
          <div class="code-title"><span>mcp.json</span><button id="copyMcpJson" type="button">复制</button></div>
          <pre id="mcpJson">__MCP_JSON__</pre>
        </div>
        <div class="code-card">
          <div class="code-title"><span>Agent prompt</span><button id="copyAgentPrompt" type="button">复制</button></div>
          <pre id="agentPrompt">__AGENT_PROMPT__</pre>
        </div>
      </div>
    </div>
  </section>
</main>

<script>
(() => {
  const baseMcpUrl = __MCP_BASE_URL_JSON__;
  const authHeader = __MCP_AUTH_HEADER_JSON__;
  const pageState = {"mode": "default"};
  const serverName = "qianji";
  let selectedMode = pageState.mode;

  function selectedMcpUrl() {
    const url = new URL(baseMcpUrl);
    url.searchParams.delete("mode");
    const mode = selectedMode;
    if (mode === "default") return url.toString();
    url.searchParams.set("mode", mode);
    return url.toString();
  }

  function mcpConfig() {
    return {
      mcpServers: {
        [serverName]: {
          type: "streamable-http",
          url: selectedMcpUrl(),
          headers: {
            Authorization: authHeader,
          },
        },
      },
      mode: selectedMode,
    };
  }

  function mcpConfigText() {
    const config = mcpConfig();
    const {mode, ...mcpJson} = config;
    return JSON.stringify(mcpJson, null, 2);
  }

  function agentPromptText() {
    return "请把下面的钱迹 MCP 服务添加到你的 MCP 配置中。\\n"
      + "服务名: qianji\\n"
      + "MCP 类型: streamable-http\\n"
      + "MCP URL: " + selectedMcpUrl() + "\\n"
      + "Authorization: " + authHeader + "\\n"
      + "mode: " + selectedMode + "\\n\\n"
      + "MCP JSON:\\n" + mcpConfigText();
  }

  function render() {
    document.querySelectorAll(".mode-button").forEach((button) => {
      button.classList.toggle("active", button.dataset.mode === selectedMode);
    });
    document.getElementById("mcpUrl").textContent = selectedMcpUrl();
    document.getElementById("mcpJson").textContent = mcpConfigText();
    document.getElementById("agentPrompt").textContent = agentPromptText();
  }

  async function writeClipboardText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.append(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }

  document.querySelectorAll(".mode-button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedMode = button.dataset.mode || "default";
      render();
    });
  });
  document.getElementById("copyMcpJson").addEventListener("click", () => writeClipboardText(mcpConfigText()));
  document.getElementById("copyAgentPrompt").addEventListener("click", () => writeClipboardText(agentPromptText()));
  render();
})();
</script>
</body>
</html>"""


MCP_CONFIG_GUIDE_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>添加 MCP - 钱迹</title>
<link rel="icon" type="image/svg+xml" href="/static/docs/favicon.svg">
<link rel="stylesheet" href="/static/base.css">
<style>
  html { background: var(--bg); }
  a { color: inherit; text-decoration: none; }
  .shell { width: min(1440px, 100%); margin: 0 auto; padding: 28px; }
  .panel {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 8px;
    box-shadow: var(--shadow);
    overflow: hidden;
  }
  .panel-head {
    padding: 24px 26px 20px;
    border-bottom: 1px solid var(--line);
    background: linear-gradient(180deg, #ffffff 0%, #f3faf7 100%);
  }
  h1 { margin: 0 0 10px; font-size: 28px; line-height: 1.2; }
  .panel-body { padding: 22px 26px 26px; color: var(--muted); font-size: 14px; }
  .panel-body a { color: var(--primary); font-weight: 650; }
</style>
</head>
<body>
__TOPBAR__

<main class="shell">
  <section class="panel">
    <div class="panel-head">
      <p class="eyebrow">MCP Setup</p>
      <h1>添加 MCP</h1>
      <p class="subtle">正在使用操作台保存的凭据生成 MCP 配置…</p>
    </div>
    <div class="panel-body">
      <p id="guideStatus">正在从浏览器本地凭据加载…</p>
      <p>本页需要钱迹凭据。请先在 <a href="/account">账号管理</a> 连接钱迹账号（可勾选“记住到本地浏览器”），然后重新打开本页。</p>
    </div>
  </section>
</main>

<script>
(() => {
  const localKey = "qianji.api.credentials";
  const sessionKey = "qianji.api.credentials.session";
  const status = document.getElementById("guideStatus");

  function base64Utf8(value) {
    const bytes = new TextEncoder().encode(value);
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary);
  }

  function readCredential() {
    const raw = localStorage.getItem(localKey) || sessionStorage.getItem(sessionKey);
    if (!raw) return null;
    try {
      const data = JSON.parse(raw);
      if (data && data.account && /^[0-9a-f]{32}$/i.test(String(data.pwd_hash || ""))) return data;
    } catch (_) {}
    return null;
  }

  const credential = readCredential();
  if (!credential) {
    status.textContent = "未在浏览器中找到已保存的钱迹凭据。";
    return;
  }
  status.textContent = "已找到本地凭据，正在生成配置…";
  const token = "qj1.md5-b64." + base64Utf8(credential.account + "~" + String(credential.pwd_hash).toLowerCase());
  fetch(window.location.pathname, {headers: {Authorization: "Bearer " + token}})
    .then(async (response) => {
      if (!response.ok) throw new Error("凭据校验失败（HTTP " + response.status + "），请回操作台重新连接。");
      const html = await response.text();
      document.open();
      document.write(html);
      document.close();
    })
    .catch((error) => {
      status.textContent = String(error.message || error);
    });
})();
</script>
</body>
</html>"""


ABOUT_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Qianji API Service</title>
<link rel="icon" type="image/svg+xml" href="/static/docs/favicon.svg">
<link rel="stylesheet" href="/static/base.css">
<style>
  :root {
    --code-bg: #0b1020;
    --code-bar: #111827;
    --code-line: #263246;
    --code-text: #e8edf7;
  }
  main.shell {
    width: min(1440px, calc(100% - 56px));
    margin: 0 auto;
    padding: 28px 0 38px;
    line-height: 1.65;
  }
  main a { color: var(--primary); }
  h1 { border-bottom: 2px solid var(--primary); padding-bottom: 10px; line-height: 1.2; }
  h2 { margin-top: 32px; line-height: 1.25; }
  .status { padding: 12px 20px; border-radius: 8px; margin: 20px 0; }
  .ok { background: #e8f5e9; border: 1px solid #16a34a; }
  .warn { background: #fff7ed; border: 1px solid #f97316; }
  code { background: #eef3f8; color: #0f172a; padding: 2px 6px; border-radius: 5px; font: 0.92em ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
  table { border-collapse: collapse; width: 100%; margin: 16px 0; background: #fff; }
  th, td { border: 1px solid var(--line); padding: 8px 12px; text-align: left; vertical-align: top; }
  th { background: var(--surface-2); }
  .endpoint { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; font-size: 0.9em; }
  .code-block { margin: 14px 0 22px; border: 1px solid var(--code-line); border-radius: 8px; overflow: hidden; background: var(--code-bg); box-shadow: 0 12px 28px rgba(15, 23, 42, 0.16); }
  .code-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 9px 13px; background: var(--code-bar); color: #cbd5e1; border-bottom: 1px solid var(--code-line); font: 600 12px ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; letter-spacing: 0; }
  .code-title .tag { color: #a7f3d0; background: rgba(23, 107, 83, 0.28); border: 1px solid rgba(167, 243, 208, 0.28); border-radius: 999px; padding: 2px 8px; white-space: nowrap; }
  .code-block pre { margin: 0; padding: 16px; overflow-x: auto; background: var(--code-bg); }
  .code-block code { display: block; min-width: max-content; padding: 0; border-radius: 0; background: transparent; color: var(--code-text); font: 13px/1.65 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; white-space: pre; }
  .code-block ::selection { background: rgba(96, 165, 250, 0.35); }
  @media (max-width: 720px) {
    main.shell { width: calc(100% - 32px); padding: 16px 0 24px; }
  }
</style>
</head>
<body>
__TOPBAR__

<main class="shell">

<h1>Qianji API Service</h1>

<p>
  钱迹 API 代理服务，提供 REST API 和 Streamable HTTP MCP。MCP 端点为
  <code>/mcp</code>，当前 MCP 模式为 <code>__MCP_MODE__</code>。
  可通过 <code>QIANJI_MCP_MODE</code> 设置为 <code>read</code>、<code>write</code>、<code>advanced</code> 或 <code>full</code>。
  单次请求可用 <code>?mode=write</code> 等 URL query 覆盖工具范围。
</p>

<div class="status __STATUS_CLASS__">
  __STATUS_MSG__
</div>

<h2>Quick Links</h2>
<ul>
  <li><a href="/login">操作台</a></li>
  <li><a href="/docs">API Docs (Swagger UI)</a></li>
  <li><a href="/redoc">API Docs (ReDoc)</a></li>
  <li><code>/mcp</code> - HTTP MCP endpoint for MCP clients</li>
  <li><code>/healthz</code> - health check</li>
</ul>

<h2>MCP Modes</h2>
<table>
  <tr><th>Mode</th><th>Tools</th></tr>
  <tr><td><code>read</code></td><td>Login and read-only query tools</td></tr>
  <tr><td><code>write</code></td><td>Read tools plus core bookkeeping tools: <code>create_expense</code>, <code>create_income</code>, <code>create_transfer</code>, their update variants, <code>sync_bills</code>, <code>set_bill_cache_ttl</code> and <code>set_tags_enabled</code></td></tr>
  <tr><td><code>advanced</code> (default)</td><td>Write tools plus refund, reimbursement, loan/debt asset, principal, collection and repayment tools</td></tr>
  <tr><td><code>full</code></td><td>All MCP tools, including low-level <code>create_bill</code>, <code>update_bill</code>, <code>delete_bill</code>, raw <code>call_api</code> and 112 upstream endpoint tools</td></tr>
</table>

<h2>Add MCP to Agent</h2>
<p>
  Agent 使用 Streamable HTTP MCP 地址：<code>__AGENT_MCP_URL__</code>。
  通过 Traefik 或域名访问本页时会自动使用当前域名；本地直接访问时使用当前本地调试地址。
  MCP 使用钱迹账号的 MD5 密码等价凭据认证，Header 支持
  <code>Authorization: Bearer qj1.md5.account~pwd_hash</code> 或
  <code>Authorization: Bearer qj1.md5-b64.base64_utf8(account~pwd_hash)</code>。
</p>
<div class="code-block">
  <div class="code-title"><span>Agent config</span><span class="tag">JSON</span></div>
<pre><code>{
  "mcpServers": {
    "qianji": {
      "type": "streamable-http",
      "url": "__AGENT_MCP_URL__",
      "headers": {
        "Authorization": "Bearer qj1.md5-b64.BASE64_ACCOUNT_TILDE_PWD_HASH"
      }
    }
  }
}</code></pre>
</div>

<h2>Key Endpoints</h2>
<table>
  <tr><th>Method</th><th>Path</th><th>Description</th></tr>
  <tr><td>POST</td><td class="endpoint">/auth/login</td><td>Validate the Authorization credentials (optionally re-checks the plaintext password against the Bearer token)</td></tr>
  <tr><td>GET</td><td class="endpoint">/auth/status</td><td>Check login status</td></tr>
  <tr><td>GET</td><td class="endpoint">/bills</td><td>List bills newest-first (cache-backed, paginated)</td></tr>
  <tr><td>GET</td><td class="endpoint">/bills/range</td><td>List bills in a time range (cache-backed)</td></tr>
  <tr><td>POST</td><td class="endpoint">/bills/sync</td><td>Sync bills from Qianji into the local cache</td></tr>
  <tr><td>POST</td><td class="endpoint">/bills</td><td>Create a new bill</td></tr>
  <tr><td>DELETE</td><td class="endpoint">/bills/{id}</td><td>Delete a bill</td></tr>
  <tr><td>GET</td><td class="endpoint">/assets</td><td>List all assets</td></tr>
  <tr><td>GET</td><td class="endpoint">/categories</td><td>List categories</td></tr>
  <tr><td>GET</td><td class="endpoint">/books</td><td>List books</td></tr>
  <tr><td>GET</td><td class="endpoint">/tags</td><td>List tags</td></tr>
  <tr><td>GET</td><td class="endpoint">/budgets</td><td>List budgets</td></tr>
  <tr><td>GET</td><td class="endpoint">/currencies</td><td>List currencies</td></tr>
  <tr><td>POST</td><td class="endpoint">/api/{ctrl}/{act}</td><td>Generic API passthrough (all 112 endpoints)</td></tr>
</table>

<h2>Usage Example</h2>
<div class="code-block">
  <div class="code-title"><span>REST API examples</span><span class="tag">curl</span></div>
<pre><code>AUTH='Authorization: Bearer qj1.md5-b64.BASE64_ACCOUNT_TILDE_PWD_HASH'

# 1. Login status
curl http://localhost:50003/auth/status -H "$AUTH"

# 2. Query bills
curl http://localhost:50003/bills -H "$AUTH"

# 3. Create a bill (expense)
curl -X POST http://localhost:50003/bills \\
  -H "$AUTH" \\
  -H "Content-Type: application/json" \\
  -d '{"bill_type":"expense","amount":25.5,"occurred_at":"2026-04-12T19:30:00+08:00","asset_id":123,"category_id":60559217,"remark":"lunch"}'

# 4. Generic API call
curl -X POST http://localhost:50003/api/asset/typelist \\
  -H "$AUTH" \\
  -H "Content-Type: application/json" -d '{}'
</code></pre>
</div>

<p style="color:#999; margin-top:40px; font-size:0.85em;">
  Qianji SDK v__APP_VERSION__ | Reverse-engineered API proxy | Local network only
</p>
</main>
</body>
</html>"""


DOCS_NAV_HEAD = '<link rel="stylesheet" href="/static/base.css">'


def _render_console_page(*, page: str, mcp_path: str) -> str:
    is_console = page == "console"
    return (
        LOGIN_PAGE_HTML.replace("__MCP_PATH_JSON__", json.dumps(mcp_path))
        .replace("__PAGE_TITLE__", "钱迹操作台" if is_console else "账号管理 - 钱迹")
        .replace("__PAGE__", page)
        .replace("__TOPBAR__", render_topbar("console" if is_console else "account"))
        .replace("__WORKSPACE_CLASS__", "" if is_console else "is-hidden")
        .replace("__LOGIN_PANEL_CLASS__", "is-hidden" if is_console else "")
    )


def render_login_page(*, mcp_path: str = "/mcp") -> str:
    return _render_console_page(page="console", mcp_path=mcp_path)


def render_account_page(*, mcp_path: str = "/mcp") -> str:
    return _render_console_page(page="account", mcp_path=mcp_path)


def render_mcp_config_page(
    *,
    agent_mcp_url: str,
    auth_header: str,
    account_label: str,
    has_credentials: bool,
) -> str:
    mcp_json = json.dumps(
        {
            "mcpServers": {
                "qianji": {
                    "type": "streamable-http",
                    "url": agent_mcp_url,
                    "headers": {"Authorization": auth_header},
                }
            }
        },
        ensure_ascii=False,
        indent=2,
    )
    agent_prompt = (
        "请把下面的钱迹 MCP 服务添加到你的 MCP 配置中。\n"
        "服务名: qianji\n"
        "MCP 类型: streamable-http\n"
        f"MCP URL: {agent_mcp_url}\n"
        f"Authorization: {auth_header}\n"
        "mode: default\n\n"
        f"MCP JSON:\n{mcp_json}"
    )
    status_class = "ok" if has_credentials else "warn"
    status = (
        f"已使用当前 Authorization 生成凭据：{escape(account_label)}"
        if has_credentials
        else "未检测到已登录账号，下面配置使用占位凭据。"
    )
    return (
        MCP_CONFIG_PAGE_HTML.replace("__MCP_BASE_URL_JSON__", json.dumps(agent_mcp_url))
        .replace("__TOPBAR__", render_topbar("mcp"))
        .replace("__MCP_AUTH_HEADER_JSON__", json.dumps(auth_header))
        .replace("__CREDENTIAL_STATUS_CLASS__", status_class)
        .replace("__CREDENTIAL_STATUS__", status)
        .replace("__MCP_URL__", escape(agent_mcp_url))
        .replace("__MCP_JSON__", escape(mcp_json))
        .replace("__AGENT_PROMPT__", escape(agent_prompt))
    )


def render_mcp_config_guide_page() -> str:
    return MCP_CONFIG_GUIDE_PAGE_HTML.replace("__TOPBAR__", render_topbar("mcp"))


def render_about_page(*, status_class: str, status_msg: str, mcp_mode: str, agent_mcp_url: str) -> str:
    return (
        ABOUT_PAGE_HTML.replace("__APP_VERSION__", escape(__version__))
        .replace("__TOPBAR__", render_topbar("about"))
        .replace("__STATUS_CLASS__", escape(status_class))
        .replace("__STATUS_MSG__", status_msg)
        .replace("__MCP_MODE__", escape(mcp_mode))
        .replace("__AGENT_MCP_URL__", escape(agent_mcp_url))
    )


def inject_docs_nav(html: str, *, active: str) -> str:
    html = html.replace("</head>", DOCS_NAV_HEAD + "</head>", 1)
    return html.replace("<body>", "<body>" + render_topbar(active), 1)
