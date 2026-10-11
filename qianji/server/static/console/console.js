(() => {
  const localKey = "qianji.api.credentials";
  const sessionKey = "qianji.api.credentials.session";
  const savedKey = "qianji.api.savedCredentials";
  const catExpandedKey = "qianji.console.categories.expanded";
  const mcpPath = window.__QJ_BOOT__.mcpPath;
  const pageMode = document.body.dataset.page === "account" ? "account" : "console";
  const $ = (id) => document.getElementById(id);
  const output = $("apiOutput");
  const state = {
    assets: [],
    categories: [],
    books: [],
    bills: [],
    cards: [],
    cardsLoaded: false,
    cardFilter: {q: "", type: ""},
    editingCardId: null,
    categoryIconGroups: [],
    catManageType: 0,
    catMode: "tree",
    catSourceFormat: "text",
    catExpanded: loadCategoryExpanded(),
    catEditing: null,
    assetDetailId: null,
    assetDetailAsset: null,
    assetBills: [],
    assetBillsPage: 1,
    assetBillsTotal: 0,
    assetBillsHasMore: false,
    assetGroupsCollapsed: new Set(),
    activeSubtab: "bills",
    tagList: [],
    tagsById: {},
    tagsByToken: {},
    selectedTagIds: [],
    editingBill: null,
    batch: {active: false, selected: new Set()},
    connected: false,
    connecting: false,
    refsLoaded: false,
    connectionVersion: 0,
    pwdHash: "",
  };
  const protectedActions = new Set([
    "assets", "categories", "books", "tags", "loadRefs", "loadTags",
    "bills", "createBill", "saveBill", "deleteBill", "genericApi",
    "refreshAssets", "saveAsset", "loadMoreAssetBills", "reloadAssetDetail",
    "refreshCards", "exportCards", "saveCard", "deleteCard", "copyCardJson",
    "refreshCategories", "saveCategory", "deleteCategory", "newRootCategory", "newSubCategory",
    "editCategory", "copyCategorySource", "downloadCategorySource",
    "sortAssets", "sortCards", "sortCategories",
  ]);

  function maskIdentifier(value) {
    const text = String(value || "");
    if (!text) return "";
    if (text.includes("@")) {
      const [local, domain = ""] = text.split("@");
      const prefix = local.length > 2 ? local.slice(0, 2) : local.slice(0, 1);
      const parts = domain.split(".");
      const suffix = parts.length > 1 ? "." + parts.pop() : "";
      const name = parts.join(".") || domain;
      return prefix + "***@" + (name ? name.slice(0, 1) + "***" : "***") + suffix;
    }
    if (text.length <= 4) return text.slice(0, 1) + "***";
    return text.slice(0, 2) + "***" + text.slice(-2);
  }

  function redactForDisplay(value, key = "") {
    const secretKeys = new Set(["password", "pwd", "pwd_hash", "token", "utoken", "authorization"]);
    const identityKeys = new Set(["email", "account", "account_id", "user_name", "username", "v"]);
    if (value == null) return value;
    if (secretKeys.has(String(key).toLowerCase())) return "<redacted>";
    if (typeof value === "string") {
      if (identityKeys.has(String(key).toLowerCase()) || value.includes("@")) return maskIdentifier(value);
      return value;
    }
    if (Array.isArray(value)) return value.map((item) => redactForDisplay(item, key));
    if (typeof value === "object") {
      const next = {};
      for (const [childKey, childValue] of Object.entries(value)) next[childKey] = redactForDisplay(childValue, childKey);
      return next;
    }
    return value;
  }

  function setOutput(value) {
    output.textContent = typeof value === "string" ? value : JSON.stringify(redactForDisplay(value), null, 2);
  }

  function setStatus(text, ok = false) {
    const status = $("loginStatus");
    status.classList.toggle("ok", ok);
    status.querySelector("span:last-child").textContent = text;
  }

  function syncConnectionState() {
    // Section visibility is fixed server-side per page (console vs account);
    // here we only keep action buttons in sync with the connection state.
    document.querySelectorAll("button[data-action]").forEach((button) => {
      const action = button.dataset.action || "";
      if (action === "copyMcpConfig") {
        button.disabled = state.connecting || !credentialsReady();
      } else {
        button.disabled = state.connecting || (protectedActions.has(action) && !state.connected);
      }
    });
  }

  function requireConnected() {
    if (!state.connected) throw new Error("请先连接钱迹账号");
  }

  function isMd5Hash(value) {
    return /^[0-9a-f]{32}$/i.test(String(value || ""));
  }

  function normalizeStoredCredential(data) {
    const account = String((data && data.account) || "").trim();
    const pwd_hash = String((data && data.pwd_hash) || "").trim().toLowerCase();
    if (!account || account.includes("~") || !isMd5Hash(pwd_hash)) return null;
    return {account, pwd_hash};
  }

  function readSavedCredentials() {
    try {
      const raw = localStorage.getItem(savedKey);
      const data = raw ? JSON.parse(raw) : {};
      if (!data || typeof data !== "object" || Array.isArray(data)) return {};
      const normalized = {};
      for (const item of Object.values(data)) {
        const credential = normalizeStoredCredential(item);
        if (credential) normalized[credential.account] = credential;
      }
      return normalized;
    } catch (_) {
      return {};
    }
  }

  function writeSavedCredentials(data) {
    localStorage.setItem(savedKey, JSON.stringify(data));
  }

  function rememberSavedCredential(data) {
    const credential = normalizeStoredCredential(data);
    if (!credential) return;
    const {account, pwd_hash} = credential;
    const saved = readSavedCredentials();
    if (saved[account] && saved[account].pwd_hash === pwd_hash) return;
    saved[account] = credential;
    writeSavedCredentials(saved);
  }

  function populateSavedAccounts(selected = "") {
    const select = $("savedAccountSelect");
    const data = readSavedCredentials();
    select.innerHTML = "";
    select.append(new Option("选择已记住账号", ""));
    for (const account of Object.keys(data).sort()) {
      const option = new Option(maskIdentifier(account), account);
      select.append(option);
    }
    if (selected && data[selected]) select.value = selected;
  }

  function readStored() {
    populateSavedAccounts();
    const localRaw = localStorage.getItem(localKey);
    const raw = localRaw || sessionStorage.getItem(sessionKey);
    if (!raw) {
      syncPasswordPlaceholder();
      renderMcpConfig();
      return false;
    }
    try {
      const data = normalizeStoredCredential(JSON.parse(raw));
      if (!data) {
        if (localRaw) localStorage.removeItem(localKey);
        else sessionStorage.removeItem(sessionKey);
        state.pwdHash = "";
        syncPasswordPlaceholder();
        renderMcpConfig();
        return false;
      }
      $("apiAccount").value = data.account || "";
      $("apiPassword").value = "";
      state.pwdHash = data.pwd_hash || "";
      syncPasswordPlaceholder();
      $("rememberCredentials").checked = Boolean(localRaw);
      if (localRaw) rememberSavedCredential(data);
      populateSavedAccounts(data.account || "");
      if (data.account) setStatus("已载入本地凭据：" + maskIdentifier(data.account), false);
      renderMcpConfig();
      return Boolean(data.account && data.pwd_hash);
    } catch (_) {
      syncPasswordPlaceholder();
      renderMcpConfig();
      return false;
    }
  }

  function writeStored() {
    const {account, pwd_hash} = credentialValues();
    const credential = normalizeStoredCredential({account, pwd_hash});
    if (!credential) throw new Error("请填写钱迹账号和密码");
    state.pwdHash = credential.pwd_hash;
    syncPasswordPlaceholder();
    const data = JSON.stringify(credential);
    sessionStorage.setItem(sessionKey, data);
    if ($("rememberCredentials").checked) {
      localStorage.setItem(localKey, data);
      const saved = readSavedCredentials();
      saved[credential.account] = credential;
      writeSavedCredentials(saved);
      populateSavedAccounts(credential.account);
    } else {
      localStorage.removeItem(localKey);
    }
  }

  function credentialValues() {
    const account = $("apiAccount").value.trim();
    const rawPassword = $("apiPassword").value;
    const pwd_hash = rawPassword ? md5Utf8(rawPassword) : state.pwdHash;
    return {account, pwd_hash};
  }

  function credentialsReady() {
    const {account, pwd_hash} = credentialValues();
    return Boolean(account && isMd5Hash(pwd_hash) && !account.includes("~"));
  }

  function base64Utf8(value) {
    const bytes = new TextEncoder().encode(value);
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary);
  }

  const md5Shifts = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
    5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
    4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
    6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
  ];
  const md5K = Array.from({length: 64}, (_, i) => Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32) >>> 0);

  function rotateLeft(value, shift) {
    return ((value << shift) | (value >>> (32 - shift))) >>> 0;
  }

  function md5WordHex(word) {
    let output = "";
    for (let i = 0; i < 4; i += 1) output += ((word >>> (i * 8)) & 255).toString(16).padStart(2, "0");
    return output;
  }

  function md5Utf8(value) {
    const input = new TextEncoder().encode(value);
    const paddedLength = (((input.length + 9 + 63) >> 6) << 6);
    const bytes = new Uint8Array(paddedLength);
    bytes.set(input);
    bytes[input.length] = 0x80;
    const view = new DataView(bytes.buffer);
    const bitLength = input.length * 8;
    view.setUint32(paddedLength - 8, bitLength >>> 0, true);
    view.setUint32(paddedLength - 4, Math.floor(bitLength / 2 ** 32), true);
    let a0 = 0x67452301;
    let b0 = 0xefcdab89;
    let c0 = 0x98badcfe;
    let d0 = 0x10325476;

    for (let offset = 0; offset < paddedLength; offset += 64) {
      let a = a0;
      let b = b0;
      let c = c0;
      let d = d0;
      for (let i = 0; i < 64; i += 1) {
        let f;
        let g;
        if (i < 16) {
          f = (b & c) | (~b & d);
          g = i;
        } else if (i < 32) {
          f = (d & b) | (~d & c);
          g = (5 * i + 1) % 16;
        } else if (i < 48) {
          f = b ^ c ^ d;
          g = (3 * i + 5) % 16;
        } else {
          f = c ^ (b | ~d);
          g = (7 * i) % 16;
        }
        const next = d;
        d = c;
        c = b;
        const word = view.getUint32(offset + g * 4, true);
        b = (b + rotateLeft((a + f + md5K[i] + word) >>> 0, md5Shifts[i])) >>> 0;
        a = next;
      }
      a0 = (a0 + a) >>> 0;
      b0 = (b0 + b) >>> 0;
      c0 = (c0 + c) >>> 0;
      d0 = (d0 + d) >>> 0;
    }

    return md5WordHex(a0) + md5WordHex(b0) + md5WordHex(c0) + md5WordHex(d0);
  }

  function syncPasswordPlaceholder() {
    $("apiPassword").placeholder = state.pwdHash ? "已保存 pwd_hash，留空使用" : "密码";
  }

  function authToken() {
    const {account, pwd_hash} = credentialValues();
    if (!account || !pwd_hash) throw new Error("请填写钱迹账号和密码");
    if (account.includes("~")) throw new Error("钱迹账号不能包含 ~");
    if (!isMd5Hash(pwd_hash)) throw new Error("钱迹密码 MD5 无效");
    return "qj1.md5-b64." + base64Utf8(account + "~" + pwd_hash);
  }

  function authHeader() {
    const token = authToken();
    writeStored();
    return "Bearer " + token;
  }

  function shouldPreserveJsonInteger(token) {
    const digits = token.startsWith("-") ? token.slice(1) : token;
    return digits.length > 15;
  }

  function quoteUnsafeJsonIntegers(text) {
    let output = "";
    let inString = false;
    let escaped = false;
    for (let i = 0; i < text.length;) {
      const ch = text[i];
      if (inString) {
        output += ch;
        if (escaped) {
          escaped = false;
        } else if (ch === "\\") {
          escaped = true;
        } else if (ch === '"') {
          inString = false;
        }
        i += 1;
        continue;
      }
      if (ch === '"') {
        inString = true;
        output += ch;
        i += 1;
        continue;
      }
      if (ch === "-" || (ch >= "0" && ch <= "9")) {
        const start = i;
        if (ch === "-") i += 1;
        const digitsStart = i;
        while (i < text.length && text[i] >= "0" && text[i] <= "9") i += 1;
        const integerToken = text.slice(start, i);
        const next = text[i];
        if (next === "." || next === "e" || next === "E" || digitsStart === i) {
          output += integerToken;
        } else if (shouldPreserveJsonInteger(integerToken)) {
          output += '"' + integerToken + '"';
        } else {
          output += integerToken;
        }
        continue;
      }
      output += ch;
      i += 1;
    }
    return output;
  }

  function parseJsonPreservingLargeInts(text) {
    return JSON.parse(quoteUnsafeJsonIntegers(text));
  }

  function currentMcpUrl() {
    return new URL(mcpPath, window.location.href).toString();
  }

  function mcpConfigText() {
    return JSON.stringify({
      mcpServers: {
        qianji: {
          type: "streamable-http",
          url: currentMcpUrl(),
          headers: {
            Authorization: "Bearer " + authToken(),
          },
        },
      },
    }, null, 2);
  }

  function renderMcpConfig() {
    const code = $("mcpConfigJson");
    const hint = $("mcpConfigHint");
    if (!credentialsReady()) {
      code.textContent = "填写钱迹账号和密码后生成 MCP JSON";
      hint.textContent = "填写账号和密码后生成 qj1.md5-b64 Authorization";
    } else if ($("apiAccount").value.trim().includes("~")) {
      code.textContent = "钱迹账号不能包含 ~";
      hint.textContent = "无法生成 MCP 配置";
    } else {
      code.textContent = mcpConfigText();
      hint.textContent = "Authorization 已使用 pwd_hash 和 base64 编码";
    }
    syncConnectionState();
  }

  async function writeClipboardText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch (_) {
        // writeText rejects when the document lacks focus or permission;
        // fall through to the textarea path.
      }
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

  async function copyMcpConfig() {
    const text = mcpConfigText();
    await writeClipboardText(text);
    setStatus("已复制 MCP JSON", state.connected);
    setOutput("MCP JSON copied.");
  }

  async function callApi(path, options = {}) {
    const headers = {
      "Authorization": authHeader(),
      ...(options.body ? {"Content-Type": "application/json"} : {}),
    };
    const response = await fetch(path, {...options, headers});
    const text = await response.text();
    let body = text;
    try { body = parseJsonPreservingLargeInts(text); } catch (_) {}
    const payload = {status: response.status, ok: response.ok, body};
    setOutput(payload);
    if (!response.ok) throw new Error(typeof body === "string" ? body : (body.detail || body.em || "请求失败"));
    return body;
  }

  function safeJsonParse(value, fallback) {
    if (typeof value !== "string") return value ?? fallback;
    try { return parseJsonPreservingLargeInts(value); } catch (_) { return fallback; }
  }

  function collectArray(payload, names) {
    const data = payload && payload.data !== undefined ? payload.data : payload;
    if (Array.isArray(data)) return data;
    for (const name of names) {
      const value = data && data[name];
      const parsed = safeJsonParse(value, value);
      if (Array.isArray(parsed)) return parsed;
      if (parsed && Array.isArray(parsed.list)) return parsed.list;
    }
    if (data && Array.isArray(data.list)) return data.list;
    return [];
  }

  function normalizeCategories(payload) {
    const roots = collectArray(payload, ["categories", "list"]);
    const flat = [];
    const walk = (category, parents = [], inheritedType = undefined) => {
      const name = category.name || category.title || String(idOf(category, ["id", "cateid", "categoryid"], ""));
      const path = [...parents, name].filter(Boolean);
      const categoryType = category.type ?? category.t ?? inheritedType;
      flat.push({
        ...category,
        type: categoryType,
        t: categoryType,
        parentName: parents[parents.length - 1] || "",
        displayName: path.join(" / "),
      });
      const children = category.sublist || category.children || category.list || [];
      for (const child of children) walk(child, path, categoryType);
    };
    for (const category of roots) walk(category);
    return flat;
  }

  function normalizeTags(payload) {
    const roots = collectArray(payload, ["groups", "tags", "list", "update"]);
    const tagList = [];
    const tagsById = {};
    const tagsByToken = {};

    const addTagToken = (token, id) => {
      const key = String(token || "").trim().toLowerCase();
      if (!key) return;
      if (!tagsByToken[key]) tagsByToken[key] = [];
      if (!tagsByToken[key].includes(id)) tagsByToken[key].push(id);
    };

    const normalizeTag = (tag, groupId = "", groupName = "") => {
      const id = String(idOf(tag, ["id", "tagid", "tagId"], ""));
      if (!id || Number(tag.status ?? 1) === 2) return null;
      const name = optionText(tag, id);
      const normalized = {
        ...tag,
        id,
        groupId: String(tag.groupid ?? tag.groupId ?? groupId ?? ""),
        groupName,
        name,
        displayName: groupName ? groupName + " / " + name : name,
      };
      if (!tagsById[id]) {
        tagList.push(normalized);
        addTagToken(id, id);
        addTagToken(name, id);
        addTagToken(normalized.displayName, id);
      }
      tagsById[id] = normalized;
      return normalized;
    };

    for (const item of roots) {
      const nestedTags = item.tags ?? item.tagList ?? item.list ?? item.children;
      const rawTags = safeJsonParse(nestedTags, nestedTags);
      if (Array.isArray(rawTags)) {
        const groupId = String(idOf(item, ["id", "groupid", "groupId"], ""));
        const groupName = optionText(item, groupId) || "未分组";
        rawTags.map((tag) => normalizeTag(tag, groupId, groupName)).filter(Boolean);
      } else {
        normalizeTag(item);
      }
    }

    state.tagsById = tagsById;
    state.tagsByToken = tagsByToken;
    return tagList;
  }

  function tagIdsFromBill(bill) {
    const tags = bill && bill.extra ? safeJsonParse(bill.extra.tags, bill.extra.tags) : (bill && bill.tagList ? bill.tagList : []);
    if (!Array.isArray(tags)) return [];
    return tags.map((tag) => typeof tag === "object" ? String(idOf(tag, ["id", "tagid", "tagId"], "")) : String(tag)).filter(Boolean);
  }

  function tagNameById(id) {
    const tag = state.tagsById[String(id)];
    return tag ? tag.displayName : String(id);
  }

  function setSelectedTags(ids) {
    const unique = [];
    for (const id of ids.map((item) => String(item)).filter(Boolean)) {
      if (!unique.includes(id)) unique.push(id);
    }
    state.selectedTagIds = unique;
    $("billTags").value = unique.map((id) => tagNameById(id)).join(", ");
  }

  function parseTagInput() {
    const tokens = $("billTags").value.split(",").map((item) => item.trim()).filter(Boolean);
    const ids = [];
    for (const token of tokens) {
      let id = "";
      if (state.tagsById[token]) {
        id = token;
      } else {
        const matches = state.tagsByToken[token.toLowerCase()] || [];
        if (matches.length === 1) {
          id = matches[0];
        } else if (matches.length > 1) {
          throw new Error("标签名称不唯一：" + token + "，请填写标签ID或“组名 / 标签名”");
        } else {
          throw new Error("未找到标签：" + token + "，请先加载标签或填写已有标签ID");
        }
      }
      if (id && !ids.includes(id)) ids.push(id);
    }
    return ids;
  }

  function normalizeBills(payload) {
    const data = payload && payload.data !== undefined ? payload.data : payload;
    const normalizeBill = (bill) => {
      if (!bill || typeof bill !== "object") return bill;
      return bill.id === undefined || bill.id === null ? bill : {...bill, id: String(bill.id)};
    };
    // New cache-backed shape: {items: [normalized Bill]}. Map normalized fields
    // back to the raw names the renderer/editor read (money/cateid/bookid/time/...).
    const fromNormalized = (bill) => {
      if (!bill || typeof bill !== "object") return bill;
      const mapped = {
        ...bill,
        money: bill.amount,
        cateid: bill.category_id,
        bookid: bill.book_id,
        assetid: bill.asset_id,
        fromid: bill.from_asset_id,
        targetid: bill.to_asset_id,
        time: bill.occurred_at,
        type: bill.type_code,
      };
      return mapped.id === undefined || mapped.id === null ? mapped : {...mapped, id: String(mapped.id)};
    };
    if (data && Array.isArray(data.items)) return data.items.map(fromNormalized);
    if (data && Array.isArray(data.changes)) return data.changes.map(normalizeBill);
    const bills = data && data.bills ? data.bills : data;
    const changeList = bills && (bills.changelist || bills.changes || bills.list);
    const parsed = safeJsonParse(changeList, changeList);
    if (Array.isArray(parsed)) return parsed.map(normalizeBill);
    if (Array.isArray(bills)) return bills.map(normalizeBill);
    return [];
  }

  function optionText(item, fallbackId) {
    return item.displayName || item.name || item.title || item.assetname || item.accountname || item.typename || String(fallbackId);
  }

  function rawOptionText(item, fallbackId) {
    return item.name || item.title || item.assetname || item.accountname || item.typename || String(fallbackId);
  }

  function idOf(item, keys, fallback = "") {
    for (const key of keys) if (item[key] !== undefined && item[key] !== null) return item[key];
    return fallback;
  }

  function assetTypeLabel(asset) {
    const type = Number(asset.type);
    const stype = Number(asset.stype);
    if (type === 5 && (stype === 51 || stype === 52)) return "借贷";
    const labels = {
      "1:11": "现金", "1:12": "储蓄卡", "1:13": "支付宝", "1:14": "微信",
      "1:15": "京东金融", "1:16": "QQ钱包", "1:17": "公积金", "1:18": "医保",
      "1:19": "数字人民币", "1:20": "Paypal", "1:100": "其它",
      "2:21": "信用卡", "2:22": "花呗", "2:23": "白条", "2:24": "美团月付",
      "2:25": "借呗", "2:26": "抖音月付", "2:27": "微信分付", "2:200": "其它信用",
      "3:31": "话费", "3:32": "公交卡", "3:33": "饭卡", "3:34": "会员卡",
      "3:35": "押金", "3:36": "Apple", "3:37": "加油卡", "3:38": "石化钱包",
      "3:39": "水电", "3:300": "充值卡",
      "4:41": "股票", "4:42": "基金", "4:43": "债券", "4:44": "黄金",
      "4:45": "保险", "4:400": "投资",
    };
    return labels[type + ":" + stype] || asset.typename || "资产";
  }

  function normalizeAssets(payload) {
    return collectArray(payload, ["assets", "list"]).map((asset) => {
      const id = idOf(asset, ["id", "assetid", "accountid"], "");
      const name = rawOptionText(asset, id);
      return {...asset, displayName: "[" + assetTypeLabel(asset) + "] " + name};
    });
  }

  function fillSelect(selectId, items, keys, placeholderValue, placeholderLabel) {
    const select = $(selectId);
    const current = select.value;
    select.innerHTML = "";
    select.append(new Option(placeholderLabel, String(placeholderValue)));
    for (const item of items) {
      const id = idOf(item, keys, "");
      if (id === "") continue;
      select.append(new Option(optionText(item, id), String(id)));
    }
    if ([...select.options].some((option) => option.value === current)) select.value = current;
  }

  function renderCategoryChips() {
    // Category shortcuts were removed from the form; category selection stays in the dropdown.
  }

  function refreshReferenceViews() {
    const type = Number($("billType").value || "0");
    const categories = type === 2 ? [] : state.categories.filter((item) => Number(item.type ?? item.t ?? type) === type);
    fillSelect("billCateId", categories, ["id", "cateid", "categoryid"], "0", "未选择");
    fillSelect("billAssetId", state.assets, ["id", "assetid", "accountid"], "-1", "未指定");
    fillSelect("billTargetAssetId", state.assets, ["id", "assetid", "accountid"], "-1", "未指定");
    fillSelect("billBookId", state.books, ["bookid", "id"], "-1", "默认账本");
    renderCategoryChips();
    $("assetCount").textContent = String(state.assets.length);
    $("categoryCount").textContent = String(state.categories.length);
    $("bookCount").textContent = String(state.books.length);
  }

  function formatTime(value) {
    const n = Number(value || 0);
    if (!n) return "";
    const date = new Date(n * 1000);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString();
  }

  function toDateInput(value) {
    const n = Number(value || 0);
    if (!n) return "";
    const date = new Date(n * 1000);
    if (Number.isNaN(date.getTime())) return "";
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function startOfDayTimestamp(value) {
    if (!value) throw new Error("请选择开始日期");
    const timestamp = Math.floor(new Date(value + "T00:00:00").getTime() / 1000);
    if (!Number.isFinite(timestamp)) throw new Error("开始日期格式错误");
    return timestamp;
  }

  function endOfDayTimestamp(value) {
    if (!value) throw new Error("请选择结束日期");
    const timestamp = Math.floor(new Date(value + "T23:59:59").getTime() / 1000);
    if (!Number.isFinite(timestamp)) throw new Error("结束日期格式错误");
    return timestamp;
  }

  function setDefaultQueryRange() {
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), 1);
    const end = new Date(now.getFullYear(), now.getMonth() + 1, 0);
    $("queryStartDate").valueAsDate = start;
    $("queryEndDate").valueAsDate = end;
  }

  function nameById(items, keys, id) {
    const text = String(id ?? "");
    const item = items.find((candidate) => keys.some((key) => String(candidate[key]) === text));
    return item ? optionText(item, text) : "";
  }

  function assetNameById(id, fallback = "") {
    return nameById(state.assets, ["id", "assetid", "accountid"], id) || fallback || "";
  }

  function categoryById(id) {
    const text = String(id ?? "");
    return state.categories.find((item) => ["id", "cateid", "categoryid"].some((key) => String(item[key]) === text)) || null;
  }

  // Mirrors the app's Bill.getMoneyColorByType: 1/11 are income (green), 2/5/20
  // are neutral (transfer/报销/退款), everything else renders as expense (red).
  // Money/marker buckets mirror the app's Bill.getMoneyColorByType: 0/10 spend
  // red, 1/11 income green, 6 (借入) income colour, 7 (借出) spend colour,
  // 5 报销 blue (color_baoxiao), 20 退款 grey (color_refund); everything else
  // (转账/收款/还款/…) stays title colour.
  function billTypeClass(type) {
    const n = Number(type || 0);
    if (n === 1 || n === 11 || n === 6) return "income";
    if (n === 0 || n === 10 || n === 7) return "expense";
    if (n === 5) return "baoxiao";
    if (n === 20) return "refund";
    return "title";
  }

  // Signs mirror the app's BillShowMoneys: 支出/债务利息支出/借出/还款 "-",
  // 收入/债权利息收入/借入/收款 "+", 转账/报销/退款 none.
  function billSign(type) {
    const n = Number(type || 0);
    if (n === 0 || n === 10 || n === 7 || n === 9) return "-";
    if (n === 1 || n === 11 || n === 6 || n === 4) return "+";
    return "";
  }

  // Category icons live on the Qianji CDN and are served same-origin through
  // /category-icons/ (cached on disk server-side). The app's Category.getIcon()
  // stores a full URL; fall back to the first-character coloured circle
  // (cate_color_*) when the icon is missing or fails to load.
  const ICON_HOST_RE = /^https?:\/\/res\d?\.qianjiapp\.com\/([A-Za-z0-9_-]+\.png)(?:[?#].*)?$/i;

  function iconBasename(icon) {
    const match = ICON_HOST_RE.exec(String(icon || "").trim());
    return match ? match[1] : "";
  }

  function iconUrl(icon) {
    const name = iconBasename(icon);
    return name ? "/category-icons/" + encodeURIComponent(name) : "";
  }

  function cateColorClass(seed) {
    const text = String(seed ?? "");
    let hash = 0;
    for (let i = 0; i < text.length; i += 1) hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
    return "cate-c" + (hash % 13 + 1);
  }

  function buildIconFallback(name, seed, asset) {
    const node = document.createElement("span");
    node.className = "cate-icon-fallback " + cateColorClass(seed) + (asset ? " asset" : "");
    node.textContent = (String(name || "?").trim() || "?").slice(0, 1);
    return node;
  }

  function buildIconNode(source, options = {}) {
    const name = options.name ?? (source && (source.name || source.catename || source.title)) ?? "";
    const seed = options.seed ?? name ?? (source && (source.id || source.cateid)) ?? "";
    const url = options.url !== undefined ? options.url : iconUrl(source && source.icon);
    if (!url) return buildIconFallback(name, seed, options.asset);
    const img = document.createElement("img");
    img.className = "cate-icon" + (options.asset ? " asset" : "");
    img.loading = "lazy";
    img.alt = "";
    img.referrerPolicy = "no-referrer";
    img.src = url;
    img.addEventListener("error", () => {
      img.replaceWith(buildIconFallback(name, seed, options.asset));
    });
    return img;
  }

  const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

  function billTimestamp(bill) {
    return Number(bill.time || bill.createtime || 0);
  }

  function dayKeyOf(bill) {
    const ts = billTimestamp(bill);
    return ts ? toDateInput(ts) : "unknown";
  }

  // Day headers follow the app's xb.e: "MM月DD日 今天/昨天/前天/周X".
  function formatDayHeading(key) {
    if (key === "unknown") return "未知日期";
    const date = new Date(key + "T00:00:00");
    if (Number.isNaN(date.getTime())) return key;
    const base = String(date.getMonth() + 1).padStart(2, "0") + "月" + String(date.getDate()).padStart(2, "0") + "日";
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diff = Math.round((today.getTime() - date.getTime()) / 86400000);
    if (diff === 0) return base + " 今天";
    if (diff === 1) return base + " 昨天";
    if (diff === 2) return base + " 前天";
    return base + " " + WEEKDAYS[date.getDay()];
  }

  // Day totals mirror the app's StatSet/StatItem mapping: 收 = 1/11,
  // 支 = 0/10, 报销 = 5; 转账/退款/借贷 stay out of the header.
  function groupBillsByDay(bills) {
    const groups = [];
    const byKey = new Map();
    for (const bill of bills) {
      const key = dayKeyOf(bill);
      let group = byKey.get(key);
      if (!group) {
        group = {key, bills: [], expense: 0, income: 0, baoxiao: 0};
        byKey.set(key, group);
        groups.push(group);
      }
      group.bills.push(bill);
      const type = Number(bill.type || 0);
      const money = Number(bill.money || 0);
      if (type === 1 || type === 11) group.income += money;
      else if (type === 0 || type === 10) group.expense += money;
      else if (type === 5) group.baoxiao += money;
    }
    return groups;
  }

  function buildBillItem(bill, options = {}) {
    const type = Number(bill.type || 0);
    const typeClass = billTypeClass(type);
    const categoryName = nameById(state.categories, ["id", "cateid", "categoryid"], bill.cateid) || bill.catename || "未分类";
    const fromAssetId = bill.fromid ?? bill.assetid;
    const assetName = type === 2
      ? [assetNameById(fromAssetId, bill.fromact), assetNameById(bill.targetid, bill.targetact)].filter(Boolean).join(" -> ")
      : assetNameById(bill.assetid, bill.assetname);
    const bookName = nameById(state.books, ["bookid", "id"], bill.bookid) || (bill.bookid !== undefined ? "账本 " + bill.bookid : "");
    const money = Number(bill.money || 0);
    const sign = billSign(type);
    const item = document.createElement("article");
    item.className = "bill-item " + typeClass + (options.extraClass ? " " + options.extraClass : "");
    if (state.editingBill && String(state.editingBill.id) === String(bill.id)) item.classList.add("selected");
    item.dataset.billId = String(bill.id || "");
    item.tabIndex = 0;
    item.title = options.title || "点击查看详情";
    item.innerHTML = `
      <div class="bill-marker"></div>
      <span class="bill-icon"></span>
      <div class="bill-main">
        <div class="bill-title"><strong></strong></div>
        <div class="bill-desc"></div>
        <div class="bill-book"></div>
      </div>
      <div>
        <div class="bill-money ${typeClass}"></div>
        <div class="bill-asset"></div>
      </div>`;
    if (options.checkable) {
      item.querySelector(".bill-icon").insertAdjacentHTML("beforebegin", '<span class="qj-check" aria-hidden="true"></span>');
      if (state.batch.selected.has(String(bill.id))) item.classList.add("checked");
      item.title = "点击选择";
    }
    item.querySelector(".bill-icon").replaceWith(buildIconNode(categoryById(bill.cateid), {name: bill.catename || categoryName, seed: bill.cateid ?? categoryName}));
    item.querySelector(".bill-title strong").textContent = bill.remark || categoryName;
    item.querySelector(".bill-desc").textContent = [categoryName, assetName].filter(Boolean).join(" · ");
    item.querySelector(".bill-book").textContent = bookName || "默认账本";
    item.querySelector(".bill-money").textContent = sign + money.toFixed(2);
    item.querySelector(".bill-asset").textContent = formatTime(bill.time);
    return item;
  }

  // Shared by the bills tab and the asset detail view: one rounded card per day
  // with a header (date + per-day totals) above its bills, mirroring the app.
  function renderBillGroups(container, bills, options = {}) {
    container.innerHTML = "";
    for (const group of groupBillsByDay(bills)) {
      const card = document.createElement("div");
      card.className = "day-group";
      const head = document.createElement("div");
      head.className = "day-head";
      head.dataset.dayKey = group.key;
      const sumParts = [];
      if (group.income > 0) sumParts.push('<span class="sum-income">收:' + group.income.toFixed(2) + "</span>");
      if (group.expense > 0) sumParts.push('<span class="sum-expense">支:' + group.expense.toFixed(2) + "</span>");
      if (group.baoxiao > 0) sumParts.push('<span class="sum-baoxiao">报销:' + group.baoxiao.toFixed(2) + "</span>");
      head.innerHTML = '<span class="day-date"></span><span class="day-sum">' + sumParts.join("") + "</span>";
      head.querySelector(".day-date").textContent = formatDayHeading(group.key);
      card.append(head);
      for (const bill of group.bills) card.append(buildBillItem(bill, options));
      container.append(card);
    }
  }

  function renderBills() {
    const list = $("billList");
    $("billCount").textContent = String(state.bills.length);
    $("billListMeta").textContent = state.bills.length ? state.bills.length + " 条记录" : "无记录";
    if (!state.bills.length) {
      list.innerHTML = '<div class="empty">没有读取到账单</div>';
      return;
    }
    const sorted = [...state.bills].sort((a, b) => billTimestamp(b) - billTimestamp(a));
    renderBillGroups(list, sorted.slice(0, 200), {checkable: state.batch.active});
  }

  /* -- bottom sheets --------------------------------------------------------
     App-style bottom sheet: a scrim + rounded sheet fixed to the viewport.
     The nodes are appended inside .workspace so the --qj-* design tokens
     resolve; fixed positioning escapes the workspace's scroll container. */
  const BILL_TYPE_LABELS = {
    0: "支出", 1: "收入", 2: "转账", 3: "不计入", 4: "收款", 5: "报销",
    6: "债务本金", 7: "债权本金", 9: "还款", 10: "债务利息", 11: "债权利息", 20: "退款",
  };

  function billTypeLabel(type) {
    return BILL_TYPE_LABELS[Number(type || 0)] || "账单";
  }

  let sheetCleanup = null;

  function closeSheet() {
    document.querySelectorAll(".qj-sheet, .qj-scrim").forEach((node) => node.remove());
    if (sheetCleanup) {
      sheetCleanup();
      sheetCleanup = null;
    }
  }

  function openSheet(build) {
    closeSheet();
    const host = document.querySelector(".workspace") || document.body;
    const scrim = document.createElement("div");
    scrim.className = "qj-scrim";
    const sheet = document.createElement("div");
    sheet.className = "qj-sheet";
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
    build(sheet);
    scrim.addEventListener("click", closeSheet);
    const onKey = (event) => {
      if (event.key === "Escape") closeSheet();
    };
    document.addEventListener("keydown", onKey);
    sheetCleanup = () => document.removeEventListener("keydown", onKey);
    host.append(scrim, sheet);
    const first = sheet.querySelector("button");
    if (first) first.focus();
    return sheet;
  }

  function sheetActionButton(label, className, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    button.addEventListener("click", () => {
      closeSheet();
      onClick();
    });
    return button;
  }

  function openBillPreview(bill) {
    const typeClass = billTypeClass(bill.type);
    const categoryName = nameById(state.categories, ["id", "cateid", "categoryid"], bill.cateid) || bill.catename || "未分类";
    const fromAssetId = bill.fromid ?? bill.assetid;
    const assetName = Number(bill.type || 0) === 2
      ? [assetNameById(fromAssetId, bill.fromact), assetNameById(bill.targetid, bill.targetact)].filter(Boolean).join(" → ")
      : assetNameById(bill.assetid, bill.assetname);
    const bookName = nameById(state.books, ["bookid", "id"], bill.bookid) || "";
    const tagNames = tagIdsFromBill(bill).map((id) => (state.tagsById[id] && state.tagsById[id].name) || id).filter(Boolean);
    openSheet((sheet) => {
      const amount = document.createElement("div");
      amount.className = "qj-sheet-amount " + typeClass;
      amount.textContent = billSign(bill.type) + Number(bill.money || 0).toFixed(2);
      const meta = document.createElement("div");
      meta.className = "qj-sheet-meta";
      const rows = [
        ["分类", categoryName],
        ["类型", billTypeLabel(bill.type)],
        ["日期", formatTime(bill.time) || "-"],
        ["资产", assetName || "-"],
        ["账本", bookName || "默认账本"],
        ["备注", bill.remark || "-"],
        ["标签", tagNames.length ? tagNames.join("、") : "-"],
        ["ID", String(bill.id ?? "-")],
      ];
      for (const [label, value] of rows) {
        const row = document.createElement("div");
        const term = document.createElement("span");
        term.textContent = label;
        const val = document.createElement("span");
        val.textContent = value;
        row.append(term, val);
        meta.append(row);
      }
      const buttons = document.createElement("div");
      buttons.className = "qj-sheet-actions";
      buttons.append(
        sheetActionButton("编辑", "secondary", () => fillBillForm(bill).catch((error) => {
          setStatus(String(error.message || error), false);
          setOutput(String(error.message || error));
        })),
        sheetActionButton("复制为新账单", "secondary", () => duplicateBill(bill)),
        sheetActionButton("删除", "danger", () => deleteBillFromPreview(bill)),
      );
      sheet.append(amount, meta, buttons);
    });
  }

  // Copies a bill into the composer as a new record (no id), mirroring the
  // app's "再记一笔" action. The date defaults to today.
  function duplicateBill(bill) {
    fillBillForm({...bill, id: undefined}).then(() => {
      state.editingBill = null;
      $("composeHint").textContent = "新账单（复制）";
      $("billDate").valueAsDate = new Date();
      syncEditMode();
      setStatus("已填入表单，保存即创建新账单", true);
    }).catch((error) => {
      setStatus(String(error.message || error), false);
      setOutput(String(error.message || error));
    });
  }

  async function reportError(error) {
    setStatus(String(error.message || error), false);
    setOutput(String(error.message || error));
  }

  // Deletes a record by id without requiring it to be open in the composer.
  async function deleteBillRecord(id) {
    await callApi("/bills/" + encodeURIComponent(id), {method: "DELETE"});
    if (state.editingBill && String(state.editingBill.id) === String(id)) clearBillForm();
    await loadBills();
    if (state.assetDetailId !== null) await refreshAssetDetail();
  }

  async function deleteBillFromPreview(bill) {
    if (!state.connected) throw new Error("请先连接钱迹账号");
    const id = String(bill.id);
    if (!window.confirm("确认删除账单 ID " + id + "？此操作不可恢复。")) return;
    try {
      await deleteBillRecord(id);
      setStatus("已删除账单 ID " + id, true);
    } catch (error) {
      await reportError(error);
    }
  }

  function assetMoney(asset) {
    const n = Number(asset && (asset.money ?? asset.balance ?? asset.amount) || 0);
    return Number.isFinite(n) ? n : 0;
  }

  function isDebtAsset(asset) {
    const type = Number(asset.type);
    const stype = Number(asset.stype);
    return type === 2 || (type === 5 && (stype === 51 || stype === 52));
  }

  function makeDataRow(title, desc, sideNodes, sub = false) {
    const row = document.createElement("div");
    row.className = "data-row" + (sub ? " sub" : "");
    row.innerHTML = `
      <div class="data-row-main">
        <div class="data-row-title"><strong></strong></div>
        <div class="data-row-desc"></div>
      </div>
      <div class="data-row-side"></div>`;
    row.querySelector(".data-row-title strong").textContent = title;
    row.querySelector(".data-row-desc").textContent = desc;
    const side = row.querySelector(".data-row-side");
    for (const node of sideNodes) side.append(node);
    return row;
  }

  function renderAssets() {
    const list = $("assetList");
    const assets = state.assets;
    let assetTotal = 0;
    let debtTotal = 0;
    for (const asset of assets) {
      if (isDebtAsset(asset)) debtTotal += assetMoney(asset);
      else assetTotal += assetMoney(asset);
    }
    $("assetViewTotal").textContent = assetTotal.toFixed(2);
    $("assetViewDebt").textContent = debtTotal.toFixed(2);
    $("assetViewNet").textContent = (assetTotal - debtTotal).toFixed(2);
    $("assetViewCount").textContent = String(assets.length);
    if (!assets.length) {
      list.innerHTML = '<div class="empty">未读取到资产，点击右上角「刷新资产」加载</div>';
      return;
    }
    list.innerHTML = "";
    const groups = new Map();
    for (const asset of assets) {
      const label = assetTypeLabel(asset);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(asset);
    }
    for (const [label, items] of groups) {
      const head = document.createElement("div");
      head.className = "data-group-head";
      head.dataset.assetGroup = label;
      // Sort mode always renders every row so the commit sees the full order.
      const collapsed = sortState.mode !== "assets" && state.assetGroupsCollapsed.has(label);
      if (collapsed) head.classList.add("collapsed");
      const groupSum = items.reduce((sum, asset) => sum + (isDebtAsset(asset) ? -Math.abs(assetMoney(asset)) : assetMoney(asset)), 0);
      head.innerHTML = '<span class="data-group-name"></span><span class="data-group-sum"></span><span class="data-group-arrow"></span>';
      head.querySelector(".data-group-name").textContent = label + "（" + items.length + "）";
      head.querySelector(".data-group-sum").textContent = groupSum.toFixed(2);
      head.querySelector(".data-group-arrow").textContent = collapsed ? "▸" : "▾";
      head.title = collapsed ? "展开分组" : "收起分组";
      list.append(head);
      if (collapsed) continue;
      for (const asset of items) {
        const id = idOf(asset, ["id", "assetid", "accountid"], "");
        const money = assetMoney(asset);
        const moneyNode = document.createElement("span");
        moneyNode.className = "data-row-money" + (isDebtAsset(asset) ? " debt" : "");
        moneyNode.textContent = (isDebtAsset(asset) ? "-" : "") + Math.abs(money).toFixed(2);
        const chevron = document.createElement("span");
        chevron.className = "data-row-open";
        chevron.textContent = "›";
        const desc = [
          asset.currency ? "币种 " + asset.currency : "",
          Number(asset.hide || 0) === 1 ? "已隐藏" : "",
          id !== "" ? "ID " + id : "",
        ].filter(Boolean).join(" · ");
        const row = makeDataRow(rawOptionText(asset, id), desc, [moneyNode, chevron]);
        row.classList.add("clickable");
        row.dataset.assetId = String(id);
        row.title = "点击查看该资产的交易记录";
        list.append(row);
      }
    }
  }

  const ASSET_BILL_PAGE_SIZE = 50;

  function unwrapAssetInfo(payload) {
    const data = payload && typeof payload === "object" && payload.data ? payload.data : payload;
    if (!data || typeof data !== "object") return null;
    const asset = data.asset || data.info || data;
    return asset && typeof asset === "object" ? asset : null;
  }

  async function fetchAssetInfo(id) {
    const payload = await callApi("/asset/info", {method: "POST", body: JSON.stringify({did: Number(id)})});
    const asset = unwrapAssetInfo(payload);
    if (!asset) throw new Error("未获取到资产信息：" + id);
    return asset;
  }

  function renderAssetDetailHeader(asset) {
    const id = idOf(asset, ["id", "assetid", "accountid"], state.assetDetailId);
    const money = assetMoney(asset);
    const debt = isDebtAsset(asset);
    $("assetDetailMoney").textContent = (debt ? "-" : "") + Math.abs(money).toFixed(2);
    $("assetDetailType").textContent = assetTypeLabel(asset);
    $("assetDetailCurrency").textContent = asset.basecurrency || asset.currency || "CNY";
    $("assetDetailId").textContent = String(id);
    $("assetViewTitle").textContent = "资产详情 · " + rawOptionText(asset, id);
    $("assetEditHint").textContent = "ID " + id + " · " + assetTypeLabel(asset);
  }

  function fillAssetEditor(asset) {
    $("assetName").value = asset && asset.name !== undefined ? rawOptionText(asset, state.assetDetailId) : "";
    $("assetRemark").value = (asset && asset.remark) || "";
  }

  function renderAssetBills() {
    const list = $("assetBillList");
    $("assetBillsMeta").textContent = state.assetBills.length
      ? state.assetBills.length + " / " + (state.assetBillsTotal || state.assetBills.length) + " 条记录"
      : "无交易记录";
    if (!state.assetBills.length) {
      list.innerHTML = '<div class="empty">该资产暂无交易记录</div>';
    } else {
      const sorted = [...state.assetBills].sort((a, b) => billTimestamp(b) - billTimestamp(a));
      renderBillGroups(list, sorted, {extraClass: "asset-bill-item", title: "点击跳到「记账」页编辑"});
    }
    setElementVisible("assetBillsMore", Boolean(state.assetBillsHasMore));
    $("assetBillsMore").disabled = !state.connected;
  }

  async function loadAssetBills(reset = false) {
    if (state.assetDetailId === null) return;
    if (reset) {
      state.assetBills = [];
      state.assetBillsPage = 1;
      state.assetBillsTotal = 0;
      state.assetBillsHasMore = false;
      $("assetBillList").innerHTML = '<div class="empty">正在加载交易记录…</div>';
    }
    const params = new URLSearchParams({
      asset_ids: String(state.assetDetailId),
      page: String(state.assetBillsPage),
      page_size: String(ASSET_BILL_PAGE_SIZE),
    });
    const payload = await callApi("/bills/search?" + params.toString());
    const items = (payload && Array.isArray(payload.items)) ? payload.items : collectArray(payload, ["items", "bills", "list"]);
    if (reset) state.assetBills = [];
    state.assetBills = state.assetBills.concat(normalizeBills({items}));
    state.assetBillsTotal = Number(payload && payload.total) || state.assetBills.length;
    state.assetBillsHasMore = Boolean(payload && payload.has_more);
    if (state.assetBillsHasMore) state.assetBillsPage += 1;
    renderAssetBills();
  }

  async function openAssetDetail(assetId) {
    const id = String(assetId);
    const cached = state.assets.find((asset) => String(idOf(asset, ["id", "assetid", "accountid"], "")) === id);
    state.assetDetailId = id;
    state.assetDetailAsset = cached || null;
    setElementVisible("assetListView", false);
    setElementVisible("assetDetailView", true);
    setElementVisible("assetBackButton", true);
    setElementVisible("assetSortButton", false);
    if (cached) renderAssetDetailHeader(cached);
    fillAssetEditor(cached || {});
    try {
      const asset = await fetchAssetInfo(id);
      state.assetDetailAsset = asset;
      renderAssetDetailHeader(asset);
      fillAssetEditor(asset);
    } catch (error) {
      setOutput(String(error.message || error));
      setStatus(String(error.message || error), false);
    }
    await loadAssetBills(true);
  }

  function closeAssetDetail() {
    state.assetDetailId = null;
    state.assetDetailAsset = null;
    state.assetBills = [];
    state.assetBillsTotal = 0;
    state.assetBillsHasMore = false;
    setElementVisible("assetDetailView", false);
    setElementVisible("assetBackButton", false);
    setElementVisible("assetSortButton", true);
    setElementVisible("assetListView", true);
    $("assetViewTitle").textContent = "资产查看";
  }

  async function refreshAssetDetail() {
    if (state.assetDetailId === null) return;
    const id = state.assetDetailId;
    try {
      const asset = await fetchAssetInfo(id);
      state.assetDetailAsset = asset;
      renderAssetDetailHeader(asset);
      fillAssetEditor(asset);
    } catch (error) {
      setOutput(String(error.message || error));
    }
    await loadAssetBills(true);
  }

  function assetUpdatePath(asset) {
    // type 5 = debt/loan asset; stype 51 = debt (借入), 52 = loan (借出)
    if (Number(asset && asset.type) === 5 && Number(asset && asset.stype) === 52) return "/assets/loans/" + state.assetDetailId;
    if (Number(asset && asset.type) === 5 && Number(asset && asset.stype) === 51) return "/assets/debts/" + state.assetDetailId;
    return "/assets/" + state.assetDetailId;
  }

  const CARD_TYPE_LABELS = {0: "普通卡", 1: "信用卡", 2: "银行存折", 3: "证券/债券", 4: "其他"};

  function cardTypeLabel(card) {
    const type = Number(card && card.type);
    return CARD_TYPE_LABELS[Number.isFinite(type) ? type : 0] || "卡片";
  }

  function cardDisplayName(card, id) {
    const bank = String((card && (card.bank || card.bankname || card.name)) || "").trim();
    return "[" + cardTypeLabel(card) + "] " + (bank || "卡片 " + id);
  }

  function formatCardNo(value) {
    const text = String(value ?? "").replace(/\s+/g, "");
    return text.replace(/(.{4})(?=.)/g, "$1 ");
  }

  function cardById(id) {
    return state.cards.find((card) => String(idOf(card, ["id", "cardid", "did"], "")) === String(id));
  }

  function filteredCards() {
    const q = state.cardFilter.q.trim().toLowerCase();
    const type = state.cardFilter.type;
    return state.cards.filter((card) => {
      if (type && cardTypeLabel(card) !== type) return false;
      if (!q) return true;
      const haystack = [
        card.bank || card.bankname || card.name || "",
        card.cardno ?? card.number ?? card.no ?? "",
        card.owner || card.holder || "",
        card.remark || "",
      ].join(" ").toLowerCase();
      return haystack.includes(q);
    });
  }

  // The app hides the type filter until more than two card types exist.
  function renderCardTypeChips() {
    const bar = $("cardTypeChips");
    bar.innerHTML = "";
    const types = [...new Set(state.cards.map((card) => cardTypeLabel(card)))];
    if (types.length <= 2) return;
    const makeChip = (label, text) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip" + (state.cardFilter.type === label ? " selected" : "");
      chip.dataset.cardTypeFilter = label;
      chip.textContent = text;
      return chip;
    };
    bar.append(makeChip("", "全部"), ...types.map((label) => makeChip(label, label)));
  }

  function renderCards() {
    const list = $("cardList");
    // Sort mode always renders every card so the commit sees the full order.
    const inSort = sortState.mode === "cards";
    setElementVisible("cardFilterBar", !inSort && state.cards.length > 0);
    renderCardTypeChips();
    const shown = inSort ? state.cards : filteredCards();
    const meta = $("cardListMeta");
    if (!state.cards.length) meta.textContent = "无卡片";
    else if (shown.length === state.cards.length) meta.textContent = state.cards.length + " 张卡片";
    else meta.textContent = shown.length + " / " + state.cards.length + " 张卡片";
    if (!state.cards.length) {
      list.innerHTML = '<div class="empty">未读取到卡片，点击右上角「刷新卡片」加载；「导出备份」可下载全部卡片 JSON</div>';
      return;
    }
    if (!shown.length) {
      list.innerHTML = '<div class="empty">没有匹配的卡片，换个关键词或类型试试</div>';
      return;
    }
    list.innerHTML = "";
    for (const card of shown) {
      const id = String(idOf(card, ["id", "cardid", "did"], ""));
      const row = document.createElement("div");
      row.className = "data-row clickable" + (id && String(state.editingCardId || "") === id ? " selected" : "");
      row.dataset.cardId = id;
      row.title = "点击查看操作";
      row.innerHTML = `
        <div class="data-row-main">
          <div class="data-row-title"><strong></strong></div>
          <div class="card-no selectable" title="点击文本即可全选，便于复制"></div>
          <div class="data-row-desc"></div>
        </div>
        <div class="data-row-side"><span class="data-row-open">›</span></div>`;
      row.querySelector(".data-row-title strong").textContent = cardDisplayName(card, id);
      const number = String(card.cardno ?? card.number ?? card.no ?? "");
      row.querySelector(".card-no").textContent = number ? formatCardNo(number) : "未填写卡号";
      const desc = [card.owner || card.holder || "", card.remark || "", id ? "ID " + id : ""].filter(Boolean).join(" · ");
      row.querySelector(".data-row-desc").textContent = desc || "无持卡人/备注";
      list.append(row);
    }
  }

  async function copyCardNumber(card, id) {
    const number = String(card.cardno ?? card.number ?? card.no ?? "").replace(/\s+/g, "");
    if (!number) throw new Error("「" + cardDisplayName(card, id) + "」未填写卡号");
    await writeClipboardText(number);
    setStatus("卡号已复制：" + formatCardNo(number), true);
  }

  async function copyCardSummary(card, id) {
    const lines = [
      "卡片: " + cardDisplayName(card, id),
      "卡号: " + String(card.cardno ?? card.number ?? card.no ?? ""),
      "持卡人: " + (card.owner || ""),
      "备注: " + (card.remark || ""),
    ];
    await writeClipboardText(lines.join("\n"));
    setStatus("卡片信息已复制", true);
  }

  async function deleteCardRecord(id) {
    const card = cardById(id);
    const name = card ? cardDisplayName(card, id) : "卡片 " + id;
    if (!window.confirm("确认删除「" + name + "」？此操作不可恢复。")) return;
    await callApi("/usercard/delete", {method: "POST", body: JSON.stringify({did: Number(id)})});
    if (String(state.editingCardId || "") === String(id)) resetCardForm();
    await refreshCards();
    setStatus("已删除「" + name + "」", true);
  }

  /* Tapping a card row opens the app's action menu (复制卡号/编辑/删除) as a
     bottom sheet instead of dropping straight into the editor; the aside form
     stays the editing surface. */
  function openCardOptions(card) {
    const id = String(idOf(card, ["id", "cardid", "did"], ""));
    const number = String(card.cardno ?? card.number ?? card.no ?? "").replace(/\s+/g, "");
    openSheet((sheet) => {
      const title = document.createElement("div");
      title.className = "qj-sheet-title";
      title.textContent = cardDisplayName(card, id);
      const list = document.createElement("div");
      list.className = "qj-sheet-list";
      const copyNumber = sheetActionButton(number ? "复制卡号 " + formatCardNo(number) : "复制卡号", "", () => {
        copyCardNumber(card, id).catch(reportError);
      });
      copyNumber.disabled = !number;
      list.append(
        copyNumber,
        sheetActionButton("复制完整信息", "", () => {
          copyCardSummary(card, id).catch(reportError);
        }),
        sheetActionButton("编辑", "", () => fillCardForm(card)),
        sheetActionButton("删除", "danger", () => {
          deleteCardRecord(id).catch(reportError);
        }),
      );
      sheet.append(title, list);
    });
  }

  function fillCardForm(card) {
    const id = String(idOf(card, ["id", "cardid", "did"], ""));
    state.editingCardId = id;
    $("cardFormTitle").textContent = "编辑卡片 ID " + id;
    $("cardEditId").value = id;
    $("cardType").value = String(card.type ?? 0);
    $("cardBank").value = card.bank || card.bankname || "";
    $("cardNo").value = String(card.cardno ?? card.number ?? "");
    $("cardOwner").value = card.owner || "";
    $("cardRemark").value = card.remark || "";
    const images = safeJsonParse(card.images, card.images);
    $("cardImages").value = Array.isArray(images) ? images.join(", ") : String(card.images || "");
    setElementVisible("cardDangerZone", true);
    renderCards();
  }

  function resetCardForm() {
    state.editingCardId = null;
    $("cardFormTitle").textContent = "新增卡片";
    $("cardEditId").value = "";
    $("cardType").value = "0";
    $("cardBank").value = "";
    $("cardNo").value = "";
    $("cardOwner").value = "";
    $("cardRemark").value = "";
    $("cardImages").value = "";
    setElementVisible("cardDangerZone", false);
    renderCards();
  }

  function currentCardPayload() {
    const id = Number($("cardEditId").value || "-1");
    const bank = $("cardBank").value.trim();
    if (!bank) throw new Error("请填写卡片名/银行");
    const images = $("cardImages").value.split(",").map((item) => item.trim()).filter(Boolean);
    return {
      id,
      cardno: $("cardNo").value.replace(/\s+/g, ""),
      type: Number($("cardType").value || "0"),
      bank,
      bankicon: "",
      owner: $("cardOwner").value.trim(),
      remark: $("cardRemark").value.trim(),
      images: JSON.stringify(images),
    };
  }

  function categoryIdOf(item) {
    return String(idOf(item, ["id", "cateid", "categoryid"], ""));
  }

  function currentManageCategories() {
    return state.categories.filter((item) => Number(item.type ?? item.t ?? 0) === state.catManageType);
  }

  function categoryParentId(item) {
    return String(idOf(item, ["parentid", "parentId"], "-1")) || "-1";
  }

  function categorySortValue(item) {
    const n = Number(item.sort ?? 0);
    return Number.isFinite(n) ? n : 0;
  }

  function buildCategoryTree(items) {
    const byId = {};
    for (const item of items) byId[categoryIdOf(item)] = item;
    const roots = [];
    const childrenOf = {};
    for (const item of items) {
      const pid = categoryParentId(item);
      if (pid !== "-1" && byId[pid]) (childrenOf[pid] = childrenOf[pid] || []).push(item);
      else roots.push(item);
    }
    const bySort = (a, b) => categorySortValue(a) - categorySortValue(b) || categoryIdOf(a).localeCompare(categoryIdOf(b));
    roots.sort(bySort);
    for (const children of Object.values(childrenOf)) children.sort(bySort);
    return {roots, childrenOf};
  }

  function loadCategoryExpanded() {
    try {
      const raw = localStorage.getItem(catExpandedKey);
      if (raw === null) return null;
      const data = JSON.parse(raw);
      if (Array.isArray(data)) return new Set(data.map(String));
    } catch (_) {}
    return new Set();
  }

  function persistCategoryExpanded() {
    try {
      if (state.catExpanded === null) localStorage.removeItem(catExpandedKey);
      else localStorage.setItem(catExpandedKey, JSON.stringify([...state.catExpanded]));
    } catch (_) {}
  }

  function isCategoryExpanded(id, hasChildren) {
    if (!hasChildren) return false;
    return state.catExpanded === null || state.catExpanded.has(String(id));
  }

  function toggleCategoryExpanded(id) {
    const key = String(id);
    if (state.catExpanded === null) {
      state.catExpanded = new Set(currentManageCategories().map((item) => categoryIdOf(item)));
    }
    if (state.catExpanded.has(key)) state.catExpanded.delete(key);
    else state.catExpanded.add(key);
    persistCategoryExpanded();
  }

  function syncCategoryMode() {
    setElementVisible("categoryTreeView", state.catMode === "tree");
    setElementVisible("categorySourceView", state.catMode === "source");
    document.querySelectorAll("button[data-cat-mode]").forEach((button) => {
      button.classList.toggle("active", button.dataset.catMode === state.catMode);
    });
    document.querySelectorAll("button[data-source-format]").forEach((button) => {
      button.classList.toggle("active", button.dataset.sourceFormat === state.catSourceFormat);
    });
  }

  // Flat rows are only rendered in sort mode now: sortUnitOf/sortPeersOf rely
  // on the .data-row / .data-row.sub[data-cate-id] shape, so the card layout
  // must not replace them there.
  function buildCategoryRow(item, childrenOf, depth) {
    const id = categoryIdOf(item);
    const children = childrenOf[id] || [];
    const editable = Number(item.editable ?? 1) !== 0;
    const row = document.createElement("div");
    row.className = "data-row" + (depth ? " sub" : "");
    row.dataset.cateId = id;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "tree-toggle placeholder";
    toggle.tabIndex = -1;
    const main = document.createElement("div");
    main.className = "data-row-main";
    main.innerHTML = '<div class="data-row-title"><strong></strong><span class="chip is-hidden" hidden>系统</span></div><div class="data-row-desc"></div>';
    main.querySelector(".data-row-title strong").textContent = rawOptionText(item, id);
    if (!editable) {
      const badge = main.querySelector(".chip");
      badge.hidden = false;
      badge.classList.remove("is-hidden");
    }
    main.querySelector(".data-row-desc").textContent = [
      children.length ? children.length + " 个子分类" : "",
      id ? "ID " + id : "",
    ].filter(Boolean).join(" · ");
    row.append(toggle, main);
    return row;
  }

  // Normal mode renders the app's 分类管理 layout: one white card per root
  // (icon + name + ⋯ + collapse arrow) with a 5-column subcategory grid and a
  // trailing 「＋添加子分类」 cell when expanded.
  function buildCategoryCard(root, children) {
    const id = categoryIdOf(root);
    const expanded = isCategoryExpanded(id, children.length > 0);
    const editable = Number(root.editable ?? 1) !== 0;
    const name = rawOptionText(root, id);
    const card = document.createElement("div");
    card.className = "cate-card";
    card.dataset.cateId = id;
    const head = document.createElement("div");
    head.className = "cate-card-head";
    head.title = children.length ? (expanded ? "收起子分类" : "展开 " + children.length + " 个子分类") : "";
    head.append(buildIconNode(root, {name, seed: id}));
    const main = document.createElement("div");
    main.className = "cate-card-main";
    main.innerHTML = '<div class="data-row-title"><strong></strong><span class="chip is-hidden" hidden>系统</span></div><div class="data-row-desc"></div>';
    main.querySelector(".data-row-title strong").textContent = name;
    if (!editable) {
      const badge = main.querySelector(".chip");
      badge.hidden = false;
      badge.classList.remove("is-hidden");
    }
    main.querySelector(".data-row-desc").textContent = children.length ? children.length + " 个子分类" : "无子分类";
    const side = document.createElement("div");
    side.className = "cate-card-side";
    if (editable) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "cate-more";
      more.dataset.cateOptions = id;
      more.title = "更多操作";
      more.textContent = "⋯";
      side.append(more);
    }
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "tree-toggle" + (children.length ? "" : " placeholder");
    toggle.dataset.cateToggle = id;
    toggle.textContent = children.length ? (expanded ? "▾" : "▸") : "";
    toggle.title = head.title;
    side.append(toggle);
    head.append(main, side);
    card.append(head);
    if (!expanded) return card;
    const grid = document.createElement("div");
    grid.className = "cate-subgrid";
    for (const child of children) {
      const childId = categoryIdOf(child);
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "cate-cell";
      cell.dataset.cateOptions = childId;
      cell.title = rawOptionText(child, childId);
      cell.append(buildIconNode(child, {name: rawOptionText(child, childId), seed: childId}));
      const label = document.createElement("span");
      label.textContent = rawOptionText(child, childId);
      cell.append(label);
      grid.append(cell);
    }
    if (editable) {
      const add = document.createElement("button");
      add.type = "button";
      add.className = "cate-cell add";
      add.dataset.action = "newSubCategory";
      add.dataset.cateId = id;
      add.innerHTML = '<span class="cate-cell-plus">＋</span><span>添加子分类</span>';
      grid.append(add);
    }
    card.append(grid);
    return card;
  }

  function captureCategoryDraft(editor) {
    if (!editor || !state.catEditing) return;
    const editing = state.catEditing;
    // Only capture when the on-screen editor still belongs to this editing
    // target — ⋯→编辑 on another category swaps catEditing before the old
    // editor leaves the DOM, and its values must not leak into the new draft.
    const mode = editing.mode === "edit" ? "edit" : "create";
    if (editor.dataset.mode !== mode) return;
    if (mode === "edit" && String(editing.id) !== String(editor.dataset.cateId || "")) return;
    if (mode === "create" && String(editing.parentId ?? "-1") !== String(editor.dataset.originalParent ?? "-1")) return;
    editing.draft = {
      name: editor.querySelector(".cate-name").value,
      // input.cate-icon: the preview <img class="cate-icon"> inside the picker
      // button would otherwise win a plain ".cate-icon" query.
      icon: editor.querySelector("input.cate-icon").value,
      parentid: editor.querySelector(".cate-parent").value,
    };
  }

  function buildCategoryEditor(items, sub) {
    const editing = state.catEditing || {};
    const isEdit = editing.mode === "edit";
    const item = isEdit ? items.find((candidate) => categoryIdOf(candidate) === String(editing.id)) : null;
    const draft = editing.draft || {};
    const container = document.createElement("div");
    container.className = "tree-editor" + (sub ? " sub" : "");
    container.dataset.mode = isEdit ? "edit" : "create";
    container.dataset.cateId = isEdit ? String(editing.id) : "";
    container.dataset.originalParent = isEdit && item ? categoryParentId(item) : String(editing.parentId ?? "-1");
    container.innerHTML = `
      <div class="tree-editor-grid">
        <label>名称<input class="cate-name" placeholder="例如 餐饮"></label>
        <label>图标
          <div class="cate-icon-field">
            <button type="button" class="cate-icon-btn" data-icon-picker>
              <span class="cate-icon-preview"></span>
              <span class="cate-icon-btn-text">选择图标</span>
            </button>
            <input class="cate-icon" type="hidden">
          </div>
        </label>
        <label>父分类<select class="cate-parent"></select></label>
      </div>
      <div class="button-row">
        <button data-action="saveCategory">保存</button>
        <button class="secondary" data-action="cancelCategoryEdit">取消</button>
        <button class="danger" data-action="deleteCategory">删除此分类</button>
      </div>
      <p class="muted"></p>`;
    container.querySelector(".cate-name").value = draft.name ?? (item ? rawOptionText(item, "") : "");
    container.querySelector("input.cate-icon").value = draft.icon ?? (item ? item.icon || "" : "");
    const parentSelect = container.querySelector(".cate-parent");
    parentSelect.append(new Option("作为一级分类", "-1"));
    const {roots} = buildCategoryTree(items);
    for (const root of roots) {
      const rootId = categoryIdOf(root);
      if (isEdit && rootId === String(editing.id)) continue;
      parentSelect.append(new Option(rawOptionText(root, rootId), rootId));
    }
    const wantedParent = draft.parentid ?? container.dataset.originalParent;
    if ([...parentSelect.options].some((option) => option.value === wantedParent)) parentSelect.value = wantedParent;
    const hint = container.querySelector(".muted");
    if (isEdit) {
      hint.textContent = "保存只提交名称/图标；父分类变化会按 App 的 changelevel/movesubcate 接口单独提交；排序请用工具栏的「调整顺序」。";
    } else {
      hint.textContent = "在当前选中位置下新增分类；删除按钮仅编辑已有分类时可用。";
      container.querySelector('[data-action="deleteCategory"]').style.display = "none";
    }
    syncCateIconField(container);
    return container;
  }

  // The icon field is a picker button + hidden input (captureCategoryDraft and
  // saveCategory keep reading `.cate-icon`.value); the button previews the
  // chosen icon and its app-side name.
  function syncCateIconField(editor) {
    const input = editor.querySelector("input.cate-icon");
    const preview = editor.querySelector(".cate-icon-preview");
    const text = editor.querySelector(".cate-icon-btn-text");
    if (!input || !preview || !text) return;
    const value = input.value.trim();
    preview.innerHTML = "";
    if (!value) {
      preview.append(buildIconFallback("?", editor.dataset.cateId || "new"));
      text.textContent = "选择图标";
      return;
    }
    const label = iconDisplayName(value);
    preview.append(buildIconNode(null, {name: label, seed: value, url: iconUrl(value)}));
    text.textContent = label;
  }

  // Friendly name for an icon URL from the loaded groups; falls back to the
  // basename without extension for custom/unknown icons.
  function iconDisplayName(url) {
    const value = String(url || "").trim();
    for (const group of state.categoryIconGroups) {
      for (const item of group.icons) {
        if (item.icon === value) return item.name;
      }
    }
    return iconBasename(value).replace(/\.png$/i, "") || value;
  }

  // App-style icon picker: group chips + icon grid + search, fed by
  // /categories/icons (group headers interleaved with icon entries).
  function openIconPicker(editor) {
    if (!state.categoryIconGroups.length) {
      setStatus("图标列表未加载，请先「刷新分类」", false);
      return;
    }
    const input = editor.querySelector("input.cate-icon");
    let activeGroup = "";
    let query = "";
    openSheet((sheet) => {
      sheet.classList.add("qj-icon-sheet");
      const title = document.createElement("div");
      title.className = "qj-sheet-title";
      title.textContent = "选择图标";
      const search = document.createElement("input");
      search.type = "search";
      search.className = "cate-icon-search";
      search.placeholder = "搜索图标名称";
      search.autocomplete = "off";
      const chips = document.createElement("div");
      chips.className = "chips cate-icon-chips";
      const grid = document.createElement("div");
      grid.className = "cate-icon-grid";
      const renderGrid = () => {
        grid.innerHTML = "";
        const q = query.trim().toLowerCase();
        let count = 0;
        for (const group of state.categoryIconGroups) {
          if (activeGroup && group.name !== activeGroup) continue;
          const icons = q
            ? group.icons.filter((item) => item.name.toLowerCase().includes(q) || item.basename.toLowerCase().includes(q))
            : group.icons;
          for (const item of icons) {
            const cell = document.createElement("button");
            cell.type = "button";
            cell.className = "cate-icon-cell" + (input.value === item.icon ? " selected" : "");
            cell.title = item.name;
            cell.append(buildIconNode(null, {name: item.name, seed: item.basename || item.name, url: iconUrl(item.icon)}));
            const label = document.createElement("span");
            label.textContent = item.name;
            cell.append(label);
            cell.addEventListener("click", () => {
              input.value = item.icon;
              syncCateIconField(editor);
              closeSheet();
            });
            grid.append(cell);
            count += 1;
          }
        }
        if (!count) grid.innerHTML = '<div class="empty">没有匹配的图标</div>';
      };
      const renderChips = () => {
        chips.innerHTML = "";
        const makeChip = (value, text) => {
          const chip = document.createElement("button");
          chip.type = "button";
          chip.className = "chip" + (activeGroup === value ? " selected" : "");
          chip.textContent = text;
          chip.addEventListener("click", () => {
            activeGroup = value;
            renderChips();
            renderGrid();
          });
          return chip;
        };
        chips.append(makeChip("", "全部"), ...state.categoryIconGroups.map((group) => makeChip(group.name, group.name)));
      };
      renderChips();
      renderGrid();
      search.addEventListener("input", () => {
        query = search.value;
        renderGrid();
      });
      sheet.append(title, search, chips, grid);
    });
  }

  function renderCategoryTree(items) {
    const list = $("categoryList");
    // Preserve an in-progress edit before wiping the DOM: a background reload
    // (reconnect, delete/move settle) re-creates the editor from catEditing.
    captureCategoryDraft(list.querySelector(".tree-editor"));
    list.innerHTML = "";
    const editing = state.catEditing;
    const editingId = editing && editing.mode === "edit" ? String(editing.id) : "";
    const createParent = editing && editing.mode === "create" ? String(editing.parentId ?? "-1") : "";
    const {roots, childrenOf} = buildCategoryTree(items);
    if (!items.length && createParent !== "-1") {
      list.innerHTML = '<div class="empty">当前类型暂无分类，点击右上角「＋ 新增一级分类」创建</div>';
      return;
    }
    // Sort mode keeps the flat row rendering (drag handles + unit logic depend
    // on it); catEditing is always null there, so no editor branches are needed.
    const inSort = sortState.mode === "categories";
    for (const root of roots) {
      const rootId = categoryIdOf(root);
      const children = childrenOf[rootId] || [];
      if (inSort) {
        list.append(buildCategoryRow(root, childrenOf, 0));
        if (!isCategoryExpanded(rootId, children.length > 0)) continue;
        for (const child of children) list.append(buildCategoryRow(child, childrenOf, 1));
        continue;
      }
      list.append(buildCategoryCard(root, children));
      const editingChild = children.some((child) => categoryIdOf(child) === editingId);
      if (editingId === rootId || createParent === rootId) list.append(buildCategoryEditor(items, false));
      else if (editingChild) list.append(buildCategoryEditor(items, true));
    }
    if (createParent === "-1") list.append(buildCategoryEditor(items, false));
  }

  function categorySourcePayload(items) {
    const {roots, childrenOf} = buildCategoryTree(items);
    const toNode = (item) => ({
      id: Number(categoryIdOf(item)) || categoryIdOf(item),
      name: rawOptionText(item, categoryIdOf(item)),
      icon: item.icon || "",
      sort: categorySortValue(item),
      children: (childrenOf[categoryIdOf(item)] || []).map(toNode),
    });
    return {
      type: state.catManageType,
      type_name: state.catManageType === 0 ? "支出" : "收入",
      exported_at: new Date().toISOString(),
      roots: roots.map(toNode),
    };
  }

  function categorySourceText(items) {
    const {roots, childrenOf} = buildCategoryTree(items);
    const lines = ["# " + (state.catManageType === 0 ? "支出" : "收入") + "分类（只读快照 " + new Date().toLocaleString() + "）"];
    const walk = (nodes, depth) => {
      for (const node of nodes) {
        const icon = node.icon ? " [" + node.icon + "]" : "";
        lines.push("  ".repeat(depth) + rawOptionText(node, categoryIdOf(node)) + icon);
        walk(childrenOf[categoryIdOf(node)] || [], depth + 1);
      }
    };
    walk(roots, 0);
    return lines.join("\n");
  }

  function categorySourceContent(items) {
    return state.catSourceFormat === "json"
      ? JSON.stringify(categorySourcePayload(items), null, 2)
      : categorySourceText(items);
  }

  function renderCategoryPanel() {
    const items = currentManageCategories();
    $("categoryListMeta").textContent = items.length ? items.length + " 个分类" : "无分类";
    syncCategoryMode();
    if (state.catMode === "source") {
      $("categorySourceCode").textContent = categorySourceContent(items);
      return;
    }
    renderCategoryTree(items);
  }

  async function submitCategoryReorder() {
    const items = currentManageCategories();
    const {roots, childrenOf} = buildCategoryTree(items);
    const groups = [];
    if (roots.length) groups.push(roots.map((root) => Number(categoryIdOf(root))));
    for (const root of roots) {
      const children = childrenOf[categoryIdOf(root)] || [];
      if (children.length) groups.push(children.map((child) => Number(categoryIdOf(child))));
    }
    await callApi("/category/reorder3", {method: "POST", body: JSON.stringify({v: JSON.stringify(groups)})});
  }

  /* -- category option sheets (App 分类管理的 ⋯ 菜单) -----------------------
     Root: 编辑 / 转为二级分类 / 删除; child: 编辑 / 转为一级分类 / 更换所属分类 /
     删除. Moves reuse the app's endpoints: changelevel (root↔child, or root →
     under a parent), movesubcate (child → another parent). */

  function startEditCategory(id) {
    state.catEditing = {mode: "edit", id: String(id)};
    // Expand the parent card so the inline editor has visible context.
    const item = state.categories.find((candidate) => categoryIdOf(candidate) === String(id));
    const pid = item ? categoryParentId(item) : "-1";
    if (pid !== "-1" && state.catExpanded !== null) {
      state.catExpanded.add(pid);
      persistCategoryExpanded();
    }
    renderCategoryPanel();
  }

  function startCreateSubCategory(parentId) {
    state.catEditing = {mode: "create", parentId: String(parentId)};
    if (state.catExpanded !== null) {
      state.catExpanded.add(String(parentId));
      persistCategoryExpanded();
    }
    renderCategoryPanel();
  }

  async function deleteCategoryById(id) {
    const item = state.categories.find((candidate) => categoryIdOf(candidate) === String(id));
    const name = item ? rawOptionText(item, id) : id;
    if (!window.confirm("确认删除分类「" + name + "」(ID " + id + ")？")) return;
    try {
      await callApi("/category/deletev2", {method: "POST", body: JSON.stringify({did: Number(id), deletebills: false})});
    } catch (error) {
      const message = String(error.message || error);
      if (!window.confirm("删除失败：" + message + "\n是否连同该分类下的账单一起删除？")) throw error;
      await callApi("/category/deletev2", {method: "POST", body: JSON.stringify({did: Number(id), deletebills: true})});
    }
    if (state.catEditing && String(state.catEditing.id || "") === String(id)) state.catEditing = null;
    await reloadCategories();
    setStatus("已删除分类「" + name + "」", true);
  }

  async function convertCategoryToRoot(id) {
    await callApi("/category/changelevel", {method: "POST", body: JSON.stringify({did: Number(id)})});
    if (state.catEditing && String(state.catEditing.id || "") === String(id)) state.catEditing = null;
    await reloadCategories();
    setStatus("已转为一级分类", true);
  }

  async function moveCategoryUnder(id, parentId, useMoveSub) {
    const endpoint = useMoveSub ? "/category/movesubcate" : "/category/changelevel";
    await callApi(endpoint, {method: "POST", body: JSON.stringify({did: Number(id), parentid: Number(parentId)})});
    if (state.catExpanded !== null) {
      state.catExpanded.add(String(parentId));
      persistCategoryExpanded();
    }
    await reloadCategories();
    setStatus("分类已移动", true);
  }

  // Second-level sheet listing candidate parents for 转为二级分类 / 更换所属分类.
  function openCategoryParentPicker(item, useMoveSub) {
    const id = categoryIdOf(item);
    const currentPid = categoryParentId(item);
    const {roots} = buildCategoryTree(currentManageCategories());
    openSheet((sheet) => {
      const title = document.createElement("div");
      title.className = "qj-sheet-title";
      title.textContent = useMoveSub ? "更换所属分类" : "转为二级分类：选择父分类";
      const list = document.createElement("div");
      list.className = "qj-sheet-list";
      let count = 0;
      for (const root of roots) {
        const rootId = categoryIdOf(root);
        if (rootId === id) continue; // can't parent to itself
        if (useMoveSub && rootId === currentPid) continue; // no-op
        count += 1;
        list.append(sheetActionButton(rawOptionText(root, rootId), "", () => {
          moveCategoryUnder(id, rootId, useMoveSub).catch(reportError);
        }));
      }
      if (!count) list.innerHTML = '<div class="empty">没有可选择的一级分类</div>';
      sheet.append(title, list);
    });
  }

  function openCategoryOptions(id) {
    const item = state.categories.find((candidate) => categoryIdOf(candidate) === String(id));
    if (!item) return;
    const isRoot = categoryParentId(item) === "-1";
    openSheet((sheet) => {
      const title = document.createElement("div");
      title.className = "qj-sheet-title";
      title.textContent = rawOptionText(item, id);
      const list = document.createElement("div");
      list.className = "qj-sheet-list";
      const entries = [sheetActionButton("编辑", "", () => startEditCategory(id))];
      if (isRoot) {
        entries.push(sheetActionButton("转为二级分类", "", () => openCategoryParentPicker(item, false)));
      } else {
        entries.push(
          sheetActionButton("转为一级分类", "", () => { convertCategoryToRoot(id).catch(reportError); }),
          sheetActionButton("更换所属分类", "", () => openCategoryParentPicker(item, true)),
        );
      }
      entries.push(sheetActionButton("删除", "danger", () => { deleteCategoryById(id).catch(reportError); }));
      list.append(...entries);
      sheet.append(title, list);
    });
  }

  // /categories/icons interleaves group header entries ({group, groupname},
  // no icon) with icon entries ({name, icon}); track the current header to
  // rebuild the app's picker groups.
  async function ensureCategoryIcons() {
    if (state.categoryIconGroups.length) return;
    const payload = await callApi("/categories/icons");
    const raw = collectArray(payload, ["icons", "list"]);
    const groups = [];
    let current = null;
    for (const entry of raw) {
      if (!entry || typeof entry !== "object") continue;
      const icon = String(entry.icon || "").trim();
      if (!icon) {
        const name = String(entry.groupname || entry.group || entry.name || "").trim();
        current = {name: name || "其它", icons: []};
        groups.push(current);
        continue;
      }
      if (!current) {
        current = {name: "常用", icons: []};
        groups.push(current);
      }
      const basename = iconBasename(icon);
      current.icons.push({name: String(entry.name || basename || "图标"), icon, basename});
    }
    state.categoryIconGroups = groups.filter((group) => group.icons.length);
  }

  async function reloadCategories() {
    state.categories = normalizeCategories(await callApi("/categories"));
    refreshReferenceViews();
    renderCategoryPanel();
  }

  async function refreshCards() {
    state.cards = collectArray(await callApi("/usercards"), ["cards", "usercards", "list"]);
    state.cardsLoaded = true;
    renderCards();
  }

  async function activateSubtab(name) {
    state.activeSubtab = name;
    document.querySelectorAll("button[data-subtab]").forEach((button) => {
      button.classList.toggle("active", button.dataset.subtab === name);
    });
    document.querySelectorAll(".subtab-panel").forEach((panel) => {
      panel.classList.toggle("is-hidden", panel.dataset.panel !== name);
    });
    if (!state.connected || pageMode !== "console") return;
    try {
      if (name === "assets") {
        await ensureRefsLoaded();
        renderAssets();
      } else if (name === "categories") {
        await ensureRefsLoaded();
        await ensureCategoryIcons();
        renderCategoryPanel();
      } else if (name === "cards" && !state.cardsLoaded) {
        await refreshCards();
      }
    } catch (error) {
      setStatus(String(error.message || error), false);
      setOutput(String(error.message || error));
    }
  }

  async function loadTags() {
    state.tagList = normalizeTags(await callApi("/tags"));
  }

  async function loadRefs() {
    const version = state.connectionVersion;
    state.refsLoaded = false;
    const [assets, categories, books, tags] = await Promise.all([
      callApi("/assets"),
      callApi("/categories"),
      callApi("/books"),
      callApi("/tags"),
    ]);
    if (version !== state.connectionVersion) return false;
    state.assets = normalizeAssets(assets);
    state.categories = normalizeCategories(categories);
    state.books = collectArray(books, ["books", "list"]);
    state.tagList = normalizeTags(tags);
    state.refsLoaded = true;
    refreshReferenceViews();
    return true;
  }

  async function ensureRefsLoaded() {
    requireConnected();
    if (!state.refsLoaded) {
      const loaded = await loadRefs();
      if (!loaded) throw new Error("连接状态已变更，请重新连接");
    }
  }

  async function loadBills() {
    // The selection refers to the previously loaded dataset; drop it.
    exitBatchMode();
    const version = state.connectionVersion;
    const bookId = $("queryBookId").value || "-1";
    const startTime = startOfDayTimestamp($("queryStartDate").value);
    const endTime = endOfDayTimestamp($("queryEndDate").value);
    if (endTime < startTime) throw new Error("结束日期不能早于开始日期");
    const params = new URLSearchParams({
      book_id: bookId,
      start_time: String(startTime),
      end_time: String(endTime),
      limit: "500",
    });
    const payload = await callApi("/bills/range?" + params.toString());
    if (version !== state.connectionVersion) return false;
    state.bills = normalizeBills(payload);
    renderBills();
    return true;
  }

  function clearLoadedData() {
    exitBatchMode();
    state.assets = [];
    state.categories = [];
    state.books = [];
    state.bills = [];
    state.cards = [];
    state.cardsLoaded = false;
    state.categoryIconGroups = [];
    state.tagList = [];
    state.tagsById = {};
    state.tagsByToken = {};
    state.selectedTagIds = [];
    state.editingBill = null;
    state.refsLoaded = false;
    state.catEditing = null;
    state.assetBills = [];
    state.assetBillsPage = 1;
    state.assetBillsTotal = 0;
    state.assetBillsHasMore = false;
    state.assetGroupsCollapsed = new Set();
    state.cardFilter = {q: "", type: ""};
    $("cardSearch").value = "";
    setSelectedTags([]);
    refreshReferenceViews();
    syncEditMode();
    renderBills();
    closeAssetDetail();
    renderAssets();
    resetCardForm();
    renderCategoryPanel();
  }

  function occurredAtFromDate() {
    const value = $("billDate").value;
    if (!value) return undefined;
    const date = new Date(value + "T12:00:00");
    return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
  }

  function setElementVisible(id, visible) {
    const element = $(id);
    element.hidden = !visible;
    element.classList.toggle("is-hidden", !visible);
    element.style.display = visible ? "" : "none";
  }

  function syncEditMode() {
    const editing = Boolean(state.editingBill);
    setElementVisible("editBillPanel", editing);
    $("createBillButton").style.display = editing ? "none" : "";
  }

  function setBillType(type, updateHint = true) {
    const selectedType = String(type ?? "0");
    document.querySelectorAll("button[data-bill-type]").forEach((button) => {
      button.classList.toggle("active", button.dataset.billType === selectedType);
      if (button.dataset.billType === selectedType && updateHint) $("composeHint").textContent = button.textContent;
    });
    $("billType").value = selectedType;
    $("billMoney").style.color = selectedType === "1" ? "var(--qj-income)" : selectedType === "2" ? "var(--qj-neutral)" : "var(--qj-expense)";
    const isTransfer = selectedType === "2";
    setElementVisible("categoryField", !isTransfer);
    setElementVisible("targetAssetField", isTransfer);
    $("assetFieldLabel").textContent = isTransfer ? "转出资产" : selectedType === "1" ? "收入资产" : "支出资产";
    $("targetAssetFieldLabel").textContent = "转入资产";
    if (isTransfer) $("billCateId").value = "0";
    if (!isTransfer) $("billTargetAssetId").value = "-1";
    refreshReferenceViews();
  }

  function setSelectValue(selectId, value, label) {
    const select = $(selectId);
    const text = String(value ?? "");
    if (!text) return;
    if (![...select.options].some((option) => option.value === text)) {
      select.append(new Option(label || text, text));
    }
    select.value = text;
  }

  async function fillBillForm(bill) {
    await ensureRefsLoaded();
    state.editingBill = bill;
    setBillType(Number(bill.type || 0), false);
    $("composeHint").textContent = "编辑 ID " + bill.id;
    $("billMoney").value = bill.money ?? "";
    $("billRemark").value = bill.remark || "";
    $("billDate").value = toDateInput(bill.time) || $("billDate").value;
    setSelectValue("billCateId", bill.cateid, nameById(state.categories, ["id", "cateid", "categoryid"], bill.cateid));
    const sourceAssetId = Number(bill.type || 0) === 2 ? (bill.fromid ?? bill.assetid) : bill.assetid;
    setSelectValue("billAssetId", sourceAssetId, assetNameById(sourceAssetId, bill.fromact || bill.assetname));
    setSelectValue("billBookId", bill.bookid, nameById(state.books, ["bookid", "id"], bill.bookid) || "账本 " + bill.bookid);
    setSelectValue("billTargetAssetId", bill.targetid, assetNameById(bill.targetid, bill.targetact));
    setSelectedTags(tagIdsFromBill(bill));
    syncEditMode();
    renderBills();
  }

  function clearBillForm(nextType = 0) {
    state.editingBill = null;
    setBillType(nextType, true);
    $("billMoney").value = "";
    $("billRemark").value = "";
    $("billDate").valueAsDate = new Date();
    setSelectedTags([]);
    $("billCateId").value = "0";
    $("billAssetId").value = "-1";
    $("billBookId").value = "-1";
    $("billTargetAssetId").value = "-1";
    syncEditMode();
    renderBills();
  }

  function billFormBody(includeEmptyTags = false) {
    const tagIds = parseTagInput();
    const tags = tagIds.map((id) => (state.tagsById[id] && state.tagsById[id].name) || id);
    const billType = Number($("billType").value || "0");
    const billTypeName = billType === 1 ? "income" : billType === 2 ? "transfer" : "expense";
    const body = {
      bill_type: billTypeName,
      amount: Number($("billMoney").value || "0"),
      book_id: Number($("billBookId").value || "-1"),
      remark: $("billRemark").value,
    };
    if (billType === 2) {
      body.from_asset_id = Number($("billAssetId").value || "-1");
      body.to_asset_id = Number($("billTargetAssetId").value || "-1");
    } else {
      body.asset_id = Number($("billAssetId").value || "-1");
      body.category_id = Number($("billCateId").value || "0");
    }
    const occurredAt = occurredAtFromDate();
    if (occurredAt) body.occurred_at = occurredAt;
    if (includeEmptyTags || tags.length) body.tags = tags;
    return body;
  }

  function markDisconnected(message = "未连接", clearData = true) {
    state.connectionVersion += 1;
    state.connected = false;
    state.connecting = false;
    state.refsLoaded = false;
    if (clearData) clearLoadedData();
    setStatus(message, false);
    syncConnectionState();
  }

  async function connectAndLoad(automatic = false) {
    const version = state.connectionVersion + 1;
    state.connectionVersion = version;
    state.connecting = true;
    state.connected = false;
    state.refsLoaded = false;
    syncConnectionState();
    const account = $("apiAccount").value.trim();
    setStatus((automatic ? "正在自动连接：" : "正在连接：") + maskIdentifier(account), false);
    try {
      const payload = await callApi("/auth/status");
      if (version !== state.connectionVersion) return;
      const accountId = payload.account_id || (payload.account && payload.account.account_id) || account;
      state.connected = true;
      setStatus("已连接：" + maskIdentifier(accountId), true);
      syncConnectionState();
      if (pageMode !== "console") return;
      const refsLoaded = await loadRefs();
      if (!refsLoaded || version !== state.connectionVersion) return;
      await loadBills();
      closeAssetDetail();
      renderAssets();
      renderCategoryPanel();
      if (state.cardsLoaded) await refreshCards();
    } catch (error) {
      state.connected = false;
      state.refsLoaded = false;
      if (version === state.connectionVersion) clearLoadedData();
      throw error;
    } finally {
      state.connecting = false;
      syncConnectionState();
    }
  }

  async function autoConnect() {
    try {
      await connectAndLoad(true);
    } catch (error) {
      const message = String(error.message || error);
      setStatus("自动连接失败：" + message, false);
      setOutput(message);
    }
  }

  // -- drag sort (资产 / 卡片 / 分类) ----------------------------------------
  // Pointer-event reorder mirroring the app's edit-mode sort: each row gets a
  // drag handle, the dragged row (a root category drags its children along)
  // follows the pointer, and a bottom bar commits (保存) or discards (取消).
  // Assets/cards commit the flat id array the app's Gson().toJson(ids) sends;
  // categories sync the DOM order back into each item's sort field and reuse
  // the proven submitCategoryReorder groups payload.
  const sortState = {mode: null, dirty: false, bar: null, drag: null, savedExpanded: undefined, savedSorts: null};
  const SORT_CONFIG = {
    assets: {
      container: "assetList",
      rowSelector: ".data-row[data-asset-id]",
      info: "按住手柄拖动调整资产顺序，完成后保存",
      idOf: (row) => row.dataset.assetId,
      commit: async (ids) => {
        await callApi("/asset/reorder", {method: "POST", body: JSON.stringify({v: JSON.stringify(ids)})});
        state.assets = normalizeAssets(await callApi("/assets"));
        refreshReferenceViews();
      },
      render: () => renderAssets(),
    },
    cards: {
      container: "cardList",
      rowSelector: ".data-row[data-card-id]",
      info: "按住手柄拖动调整卡片顺序，完成后保存",
      idOf: (row) => row.dataset.cardId,
      commit: async (ids) => {
        await callApi("/usercard/reorder", {method: "POST", body: JSON.stringify({v: JSON.stringify(ids)})});
        await refreshCards();
      },
      render: () => renderCards(),
    },
    categories: {
      container: "categoryList",
      rowSelector: ".data-row[data-cate-id]",
      info: "拖动一级分类会连同其子分类整体移动；子分类只能在其父分类内拖动",
      idOf: (row) => row.dataset.cateId,
      commit: async () => {
        syncCategoryOrderFromDom();
        await submitCategoryReorder();
        await reloadCategories();
      },
      render: () => renderCategoryPanel(),
    },
  };

  function sortConfig() { return SORT_CONFIG[sortState.mode]; }
  function sortContainerEl() { return $(sortConfig().container); }
  function sortRowEls() { return [...sortContainerEl().querySelectorAll(sortConfig().rowSelector)]; }

  // The dragged block: a root category moves together with its children rows.
  function sortUnitOf(row) {
    if (sortState.mode !== "categories" || row.classList.contains("sub")) return [row];
    const unit = [row];
    let next = row.nextElementSibling;
    while (next && next.matches(".data-row.sub[data-cate-id]")) {
      unit.push(next);
      next = next.nextElementSibling;
    }
    return unit;
  }

  // Rows the dragged row may be dropped relative to: same level only — roots
  // among roots, children within their own parent's run.
  function sortPeersOf(row) {
    const config = sortConfig();
    if (sortState.mode !== "categories") {
      return [...sortContainerEl().querySelectorAll(config.rowSelector)].filter((peer) => peer !== row);
    }
    if (row.classList.contains("sub")) {
      const peers = [];
      let prev = row.previousElementSibling;
      while (prev && prev.matches(".data-row.sub[data-cate-id]")) {
        peers.unshift(prev);
        prev = prev.previousElementSibling;
      }
      let next = row.nextElementSibling;
      while (next && next.matches(".data-row.sub[data-cate-id]")) {
        peers.push(next);
        next = next.nextElementSibling;
      }
      return peers;
    }
    return [...sortContainerEl().querySelectorAll(".data-row[data-cate-id]:not(.sub)")].filter((peer) => peer !== row);
  }

  // Drop target when the pointer is below every peer: the node after the last
  // peer's whole block, so a root lands after its predecessor's children too.
  function sortEndAnchor(lastPeer) {
    if (!lastPeer) return null;
    if (sortState.mode === "categories" && !lastPeer.classList.contains("sub")) {
      const unit = sortUnitOf(lastPeer);
      return unit[unit.length - 1].nextElementSibling;
    }
    return lastPeer.nextElementSibling;
  }

  function sortOrderIds() {
    return sortRowEls().map((row) => {
      const id = String(sortConfig().idOf(row) || "");
      const n = Number(id);
      return id !== "" && Number.isSafeInteger(n) ? n : id;
    });
  }

  // Copies the dragged DOM order into each item's sort field so the existing
  // data-driven submitCategoryReorder emits the new groups payload.
  function syncCategoryOrderFromDom() {
    const byId = {};
    for (const item of currentManageCategories()) byId[categoryIdOf(item)] = item;
    let rootSort = 0;
    let childSort = 0;
    for (const row of sortContainerEl().querySelectorAll(".data-row[data-cate-id]")) {
      const item = byId[row.dataset.cateId];
      if (!item) continue;
      if (row.classList.contains("sub")) {
        item.sort = childSort;
        childSort += 1;
      } else {
        item.sort = rootSort;
        rootSort += 1;
        childSort = 0;
      }
    }
  }

  function showSortBar() {
    const bar = document.createElement("div");
    bar.className = "qj-bar";
    const info = document.createElement("span");
    info.className = "qj-bar-info";
    info.textContent = sortConfig().info;
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary";
    cancel.textContent = "取消";
    cancel.addEventListener("click", () => { void exitSortMode(false); });
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "保存顺序";
    save.addEventListener("click", () => { void exitSortMode(true); });
    bar.append(info, cancel, save);
    (document.querySelector(".workspace") || document.body).append(bar);
    sortState.bar = bar;
  }

  function enterSortMode(mode) {
    if (sortState.mode) return;
    const config = SORT_CONFIG[mode];
    const container = $(config.container);
    // Categories render cards outside sort mode (flat rows only exist once the
    // sort render runs), so check the underlying data instead of the DOM.
    const hasRows = mode === "categories"
      ? currentManageCategories().length > 0
      : Boolean(container.querySelector(config.rowSelector));
    if (!hasRows) {
      setStatus("没有可排序的条目", false);
      return;
    }
    if (mode === "categories") {
      if (state.catMode !== "tree") {
        setStatus("请切换到「界面」视图再排序", false);
        return;
      }
      state.catEditing = null;
      // Snapshot collapse state and per-item sort fields so 取消 restores both.
      sortState.savedExpanded = state.catExpanded === null ? null : new Set(state.catExpanded);
      sortState.savedSorts = new Map(currentManageCategories().map((item) => [categoryIdOf(item), item.sort]));
      state.catExpanded = new Set(currentManageCategories().map((item) => categoryIdOf(item)));
    }
    sortState.mode = mode;
    sortState.dirty = false;
    config.render();
    container.classList.add("sort-mode");
    for (const row of sortRowEls()) {
      const handle = document.createElement("span");
      handle.className = "drag-handle";
      handle.title = "拖动排序";
      row.prepend(handle);
    }
    showSortBar();
  }

  async function exitSortMode(save) {
    const mode = sortState.mode;
    if (!mode) return;
    const config = SORT_CONFIG[mode];
    if (save && sortState.dirty) {
      try {
        await config.commit(sortOrderIds());
        setStatus("顺序已保存", true);
      } catch (error) {
        setStatus(String(error.message || error), false);
        setOutput(String(error.message || error));
        return; // stay in sort mode so the user can retry or cancel
      }
    }
    sortState.mode = null;
    sortState.drag = null;
    sortState.dirty = false;
    $(config.container).classList.remove("sort-mode");
    if (sortState.bar) {
      sortState.bar.remove();
      sortState.bar = null;
    }
    if (mode === "categories") {
      if (!save && sortState.savedSorts) {
        const byId = {};
        for (const item of currentManageCategories()) byId[categoryIdOf(item)] = item;
        for (const [id, value] of sortState.savedSorts) if (byId[id]) byId[id].sort = value;
      }
      state.catExpanded = sortState.savedExpanded === undefined ? state.catExpanded : sortState.savedExpanded;
      persistCategoryExpanded();
      sortState.savedExpanded = undefined;
      sortState.savedSorts = null;
    }
    config.render();
  }

  function onSortPointerDown(event) {
    if (!sortState.mode) return;
    const handle = event.target.closest(".drag-handle");
    if (!handle || !sortContainerEl().contains(handle)) return;
    const row = handle.closest(sortConfig().rowSelector);
    if (!row) return;
    event.preventDefault();
    const unit = sortUnitOf(row);
    for (const el of unit) el.classList.add("dragging");
    sortState.drag = {
      row,
      unit,
      pointerId: event.pointerId,
      offsetY: event.clientY - row.getBoundingClientRect().top,
      ty: 0,
    };
    // Capture keeps the drag alive outside the row; harmless if the pointer
    // is already gone (the document-level listeners still see the moves).
    try { row.setPointerCapture(event.pointerId); } catch (_) {}
  }

  function onSortPointerMove(event) {
    const drag = sortState.drag;
    if (!drag || event.pointerId !== drag.pointerId) return;
    const container = sortContainerEl();
    const cRect = container.getBoundingClientRect();
    const edge = 40;
    if (event.clientY < cRect.top + edge) container.scrollTop -= Math.ceil((cRect.top + edge - event.clientY) / 6);
    else if (event.clientY > cRect.bottom - edge) container.scrollTop += Math.ceil((event.clientY - (cRect.bottom - edge)) / 6);
    const peers = sortPeersOf(drag.row);
    let before = null;
    for (const peer of peers) {
      const r = peer.getBoundingClientRect();
      if (event.clientY < r.top + r.height / 2) {
        before = peer;
        break;
      }
    }
    const anchor = before || sortEndAnchor(peers[peers.length - 1]);
    const last = drag.unit[drag.unit.length - 1];
    if (last.nextElementSibling !== anchor) {
      for (const el of drag.unit) container.insertBefore(el, anchor);
      sortState.dirty = true;
    }
    const rect = drag.row.getBoundingClientRect();
    drag.ty = event.clientY - drag.offsetY - (rect.top - drag.ty);
    for (const el of drag.unit) el.style.transform = "translateY(" + drag.ty + "px)";
  }

  function endSortDrag(event) {
    const drag = sortState.drag;
    if (!drag || (event && event.pointerId !== drag.pointerId)) return;
    for (const el of drag.unit) {
      el.classList.remove("dragging");
      el.style.transform = "";
    }
    if (event && drag.row.hasPointerCapture(event.pointerId)) {
      try { drag.row.releasePointerCapture(event.pointerId); } catch (_) {}
    }
    sortState.drag = null;
    if (sortState.dirty && sortState.bar) {
      const info = sortState.bar.querySelector(".qj-bar-info");
      if (info) info.textContent = "顺序已调整，记得保存";
    }
  }

  /* -- bill batch selection (长按多选) --------------------------------------
     Long-press a bill row to enter batch mode: rows grow check circles and a
     bottom bar offers 全选/取消/删除. Deletion goes through
     billbatch/deletev2 with the app's `billIds` JSON-array string. */
  const BATCH_LONG_PRESS_MS = 500;
  let batchBar = null;
  let batchLongPress = null;
  // Swallow the click that trails a fired long-press, or it would instantly
  // deselect the row the long-press just selected. Only that row's click is
  // swallowed — a fast deliberate tap on a different row must still toggle.
  let swallowBillClickId = "";

  // Selection scope = exactly what renderBills displays (sorted, capped).
  function batchBillIds() {
    return [...state.bills]
      .sort((a, b) => billTimestamp(b) - billTimestamp(a))
      .slice(0, 200)
      .map((bill) => String(bill.id || ""))
      .filter(Boolean);
  }

  function updateBatchBar() {
    if (!batchBar) return;
    batchBar.querySelector(".qj-bar-info").textContent = "已选 " + state.batch.selected.size + " 条";
    const total = batchBillIds().length;
    const selectAll = batchBar.querySelector('[data-batch-action="selectAll"]');
    if (selectAll) selectAll.textContent = total > 0 && state.batch.selected.size >= total ? "取消全选" : "全选";
    const del = batchBar.querySelector('[data-batch-action="delete"]');
    if (del) del.disabled = state.batch.selected.size === 0;
  }

  function showBatchBar() {
    const bar = document.createElement("div");
    bar.className = "qj-bar";
    const info = document.createElement("span");
    info.className = "qj-bar-info";
    const selectAll = document.createElement("button");
    selectAll.type = "button";
    selectAll.className = "secondary";
    selectAll.dataset.batchAction = "selectAll";
    selectAll.addEventListener("click", toggleBatchSelectAll);
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary";
    cancel.textContent = "取消";
    cancel.addEventListener("click", () => exitBatchMode());
    const del = document.createElement("button");
    del.type = "button";
    del.className = "bar-danger";
    del.dataset.batchAction = "delete";
    del.textContent = "删除";
    del.addEventListener("click", () => {
      deleteBatchBills().catch((error) => {
        setStatus(String(error.message || error), false);
        setOutput(String(error.message || error));
      });
    });
    bar.append(info, selectAll, cancel, del);
    (document.querySelector(".workspace") || document.body).append(bar);
    batchBar = bar;
    updateBatchBar();
  }

  function enterBatchMode(billId) {
    if (state.batch.active || sortState.mode || pageMode !== "console") return;
    if (!state.bills.length) return;
    state.batch.active = true;
    state.batch.selected = new Set(billId ? [String(billId)] : []);
    closeSheet();
    const workspace = document.querySelector(".workspace");
    if (workspace) workspace.classList.add("batch-mode");
    renderBills();
    showBatchBar();
  }

  function exitBatchMode() {
    if (!state.batch.active) return;
    state.batch.active = false;
    state.batch.selected.clear();
    const workspace = document.querySelector(".workspace");
    if (workspace) workspace.classList.remove("batch-mode");
    if (batchBar) {
      batchBar.remove();
      batchBar = null;
    }
    renderBills();
  }

  function toggleBatchBill(id, row) {
    const key = String(id);
    if (state.batch.selected.has(key)) {
      state.batch.selected.delete(key);
      row.classList.remove("checked");
    } else {
      state.batch.selected.add(key);
      row.classList.add("checked");
    }
    updateBatchBar();
  }

  function toggleBatchSelectAll() {
    const all = batchBillIds();
    const rows = [...$("billList").querySelectorAll(".bill-item[data-bill-id]")];
    if (all.length > 0 && state.batch.selected.size >= all.length) {
      state.batch.selected.clear();
      for (const row of rows) row.classList.remove("checked");
    } else {
      state.batch.selected = new Set(all);
      for (const row of rows) row.classList.add("checked");
    }
    updateBatchBar();
  }

  async function deleteBatchBills() {
    const ids = [...state.batch.selected];
    if (!ids.length) return;
    if (!window.confirm("确认删除选中的 " + ids.length + " 条账单？此操作不可恢复。")) return;
    // Join numeric id strings without Number() so ids beyond 2^53 survive.
    const idsJson = ids.every((id) => /^\d+$/.test(id)) ? "[" + ids.join(",") + "]" : JSON.stringify(ids);
    await callApi("/billbatch/deletev2", {method: "POST", body: JSON.stringify({ids: idsJson})});
    if (state.editingBill && state.batch.selected.has(String(state.editingBill.id))) clearBillForm();
    const count = ids.length;
    exitBatchMode();
    await loadBills();
    if (state.assetDetailId !== null) await refreshAssetDetail();
    setStatus("已删除 " + count + " 条账单", true);
  }

  function cancelBatchLongPress() {
    if (!batchLongPress) return;
    clearTimeout(batchLongPress.timer);
    batchLongPress = null;
  }

  const actions = {
    login: async () => {
      await connectAndLoad(false);
    },
    refreshAll: async () => {
      await connectAndLoad(false);
    },
    loadRefs,
    loadTags,
    bills: async () => {
      await ensureRefsLoaded();
      await loadBills();
    },
    newBill: () => {
      if (state.editingBill) clearBillForm();
      $("billMoney").focus();
    },
    assets: async () => { state.assets = normalizeAssets(await callApi("/assets")); refreshReferenceViews(); },
    categories: async () => { state.categories = normalizeCategories(await callApi("/categories")); refreshReferenceViews(); },
    refreshAssets: async () => {
      await ensureRefsLoaded();
      if (state.assetDetailId !== null) await refreshAssetDetail();
      else renderAssets();
    },
    saveAsset: async () => {
      if (state.assetDetailId === null) throw new Error("请先选择一个资产");
      const asset = state.assetDetailAsset || {};
      const name = $("assetName").value.trim();
      if (!name) throw new Error("请填写资产名称");
      const body = {name, remark: $("assetRemark").value.trim()};
      await callApi(assetUpdatePath(asset), {method: "PATCH", body: JSON.stringify(body)});
      state.assets = normalizeAssets(await callApi("/assets"));
      refreshReferenceViews();
      await refreshAssetDetail();
    },
    backToAssets: () => closeAssetDetail(),
    sortAssets: () => enterSortMode("assets"),
    sortCards: () => enterSortMode("cards"),
    sortCategories: () => enterSortMode("categories"),
    loadMoreAssetBills: async () => {
      await loadAssetBills(false);
    },
    reloadAssetDetail: async () => {
      await refreshAssetDetail();
    },
    refreshCards,
    exportCards: async () => {
      if (!state.cardsLoaded) await refreshCards();
      const payload = {
        exported_at: new Date().toISOString(),
        count: state.cards.length,
        cards: state.cards,
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], {type: "application/json"});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "qianji-cards-" + new Date().toISOString().slice(0, 10) + ".json";
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setOutput("已导出 " + state.cards.length + " 张卡片备份。");
    },
    saveCard: async () => {
      const payload = currentCardPayload();
      await callApi("/usercard/submit", {method: "POST", body: JSON.stringify({v: JSON.stringify(payload)})});
      state.editingCardId = payload.id === -1 ? null : String(payload.id);
      await refreshCards();
      if (payload.id === -1) resetCardForm();
      setStatus("卡片已保存", true);
    },
    resetCardForm,
    copyCardJson: async () => {
      if (state.editingCardId === null) throw new Error("请先点击卡片选择「编辑」载入一张卡片");
      const card = cardById(state.editingCardId);
      if (!card) throw new Error("未找到卡片 ID " + state.editingCardId);
      await writeClipboardText(JSON.stringify(card, null, 2));
      setStatus("卡片 JSON 已复制", true);
    },
    deleteCard: async () => {
      const id = String($("cardEditId").value || "");
      if (!id) throw new Error("请先点击卡片选择「编辑」载入要删除的卡片");
      await deleteCardRecord(id);
    },
    refreshCategories: async () => {
      await ensureRefsLoaded();
      await ensureCategoryIcons();
      await reloadCategories();
    },
    saveCategory: async () => {
      await ensureRefsLoaded();
      const editor = document.querySelector(".tree-editor");
      const name = editor.querySelector(".cate-name").value.trim();
      if (!name) throw new Error("请填写分类名称");
      const icon = editor.querySelector("input.cate-icon").value.trim();
      const parentId = Number(editor.querySelector(".cate-parent").value || "-1");
      const editingId = String(editor.dataset.cateId || "");
      const originalParent = Number(editor.dataset.originalParent || "-1");
      const isEdit = editor.dataset.mode === "edit";
      const did = isEdit ? Number(editingId) : -1;
      const body = {bookid: -1, v: name, t: state.catManageType};
      if (did > 0) body.did = did;
      if (parentId > 0) body.parentid = parentId;
      if (icon) body.icon = icon;
      await callApi("/category/submitv2", {method: "POST", body: JSON.stringify(body)});
      if (isEdit) {
        if (parentId <= 0 && originalParent > 0) {
          await callApi("/category/changelevel", {method: "POST", body: JSON.stringify({did})});
        } else if (parentId > 0 && parentId !== originalParent) {
          const endpoint = originalParent > 0 ? "/category/movesubcate" : "/category/changelevel";
          await callApi(endpoint, {method: "POST", body: JSON.stringify({did, parentid: parentId})});
        }
      } else if (parentId > 0 && state.catExpanded !== null) {
        state.catExpanded.add(String(parentId));
        persistCategoryExpanded();
      }
      state.catEditing = null;
      await reloadCategories();
    },
    cancelCategoryEdit: () => {
      state.catEditing = null;
      renderCategoryPanel();
    },
    newRootCategory: () => {
      state.catEditing = {mode: "create", parentId: "-1"};
      renderCategoryPanel();
    },
    newSubCategory: (button) => {
      const id = String((button && button.dataset.cateId) || "");
      startCreateSubCategory(id);
    },
    editCategory: (button) => {
      const id = String((button && button.dataset.cateId) || "");
      if (!id) throw new Error("缺少分类 ID");
      startEditCategory(id);
    },
    deleteCategory: async (button) => {
      const editor = button && button.closest(".tree-editor");
      const id = String((editor && editor.dataset.cateId) || "");
      if (!id) throw new Error("请先点击分类的「⋯」选择删除");
      await deleteCategoryById(id);
    },
    copyCategorySource: async () => {
      await writeClipboardText(categorySourceContent(currentManageCategories()));
      setStatus("分类源码已复制", true);
    },
    downloadCategorySource: async () => {
      const json = state.catSourceFormat === "json";
      const blob = new Blob([categorySourceContent(currentManageCategories())], {
        type: json ? "application/json" : "text/plain",
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "qianji-categories-" + (state.catManageType === 0 ? "expense" : "income")
        + "-" + new Date().toISOString().slice(0, 10) + (json ? ".json" : ".txt");
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setStatus("分类源码已下载", true);
    },
    books: async () => { state.books = collectArray(await callApi("/books"), ["books", "list"]); refreshReferenceViews(); },
    tags: loadTags,
    createBill: async () => {
      await ensureRefsLoaded();
      const body = billFormBody(false);
      await callApi("/bills", {method: "POST", body: JSON.stringify(body)});
      clearBillForm();
      await loadBills();
      if (state.assetDetailId !== null) await refreshAssetDetail();
    },
    saveBill: async () => {
      if (!state.editingBill || !state.editingBill.id) throw new Error("请先从账单列表选择要修改的账单");
      await ensureRefsLoaded();
      const body = billFormBody(true);
      await callApi("/bills/" + encodeURIComponent(state.editingBill.id), {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      clearBillForm();
      await loadBills();
      if (state.assetDetailId !== null) await refreshAssetDetail();
    },
    cancelEdit: () => clearBillForm(),
    deleteBill: async () => {
      if (!state.editingBill || !state.editingBill.id) throw new Error("请先从账单列表选择要删除的账单");
      const id = String(state.editingBill.id);
      if (!window.confirm("确认删除账单 ID " + id + "？此操作不可恢复。")) return;
      await deleteBillRecord(id);
    },
    genericApi: () => {
      const ctrl = $("genericCtrl").value.trim();
      const act = $("genericAct").value.trim();
      if (!ctrl || !act) throw new Error("请填写 controller 和 action");
      let params = {};
      try { params = JSON.parse($("genericParams").value || "{}"); }
      catch (error) { throw new Error("Params JSON 格式错误: " + error.message); }
      return callApi("/api/" + encodeURIComponent(ctrl) + "/" + encodeURIComponent(act), {
        method: "POST",
        body: JSON.stringify({params}),
      });
    },
    copyMcpConfig,
    clearCredentials: () => {
      localStorage.removeItem(localKey);
      localStorage.removeItem(savedKey);
      sessionStorage.removeItem(sessionKey);
      state.pwdHash = "";
      $("apiAccount").value = "";
      $("apiPassword").value = "";
      syncPasswordPlaceholder();
      populateSavedAccounts();
      $("rememberCredentials").checked = false;
      renderMcpConfig();
      markDisconnected("未连接", true);
      setOutput("Local credentials cleared.");
    },
  };

  function handleCredentialInput() {
    state.pwdHash = "";
    syncPasswordPlaceholder();
    renderMcpConfig();
    if (state.connected || state.connecting || state.refsLoaded || state.bills.length) {
      markDisconnected("凭据已变更，请重新连接", true);
    } else {
      syncConnectionState();
    }
  }

  $("apiAccount").addEventListener("input", handleCredentialInput);
  $("apiPassword").addEventListener("input", handleCredentialInput);

  $("savedAccountSelect").addEventListener("change", (event) => {
    const account = event.target.value;
    const saved = readSavedCredentials();
    const credential = normalizeStoredCredential(saved[account]);
    if (!account || !credential) return;
    $("apiAccount").value = credential.account || account;
    $("apiPassword").value = "";
    state.pwdHash = credential.pwd_hash;
    syncPasswordPlaceholder();
    $("rememberCredentials").checked = true;
    sessionStorage.setItem(sessionKey, JSON.stringify(credential));
    renderMcpConfig();
    markDisconnected("已选择：" + maskIdentifier(account), true);
  });

  $("cardSearch").addEventListener("input", (event) => {
    state.cardFilter.q = event.target.value;
    // Re-rendering mid-sort would wipe the drag handles; the filter bar is
    // hidden in sort mode anyway.
    if (!sortState.mode) renderCards();
  });

  document.addEventListener("click", async (event) => {
    // Sort mode is modal: row/button clicks stay inert until 取消/保存 (the
    // bar's own buttons use direct listeners, so they are unaffected).
    if (sortState.mode) return;

    const subtabButton = event.target.closest("button[data-subtab]");
    if (subtabButton) {
      await activateSubtab(subtabButton.dataset.subtab || "bills");
      return;
    }

    const catTypeButton = event.target.closest("button[data-cat-type]");
    if (catTypeButton) {
      state.catManageType = Number(catTypeButton.dataset.catType || "0");
      document.querySelectorAll("button[data-cat-type]").forEach((button) => {
        button.classList.toggle("active", button === catTypeButton);
      });
      state.catEditing = null;
      renderCategoryPanel();
      return;
    }

    const catModeButton = event.target.closest("button[data-cat-mode]");
    if (catModeButton) {
      state.catMode = catModeButton.dataset.catMode === "source" ? "source" : "tree";
      renderCategoryPanel();
      return;
    }

    const sourceFormatButton = event.target.closest("button[data-source-format]");
    if (sourceFormatButton) {
      state.catSourceFormat = sourceFormatButton.dataset.sourceFormat === "json" ? "json" : "text";
      renderCategoryPanel();
      return;
    }

    const iconPickerButton = event.target.closest("[data-icon-picker]");
    if (iconPickerButton) {
      const editor = iconPickerButton.closest(".tree-editor");
      if (editor) openIconPicker(editor);
      return;
    }

    const cateOptions = event.target.closest("[data-cate-options]");
    if (cateOptions) {
      openCategoryOptions(cateOptions.dataset.cateOptions);
      return;
    }

    const cateToggle = event.target.closest("button[data-cate-toggle]");
    if (cateToggle) {
      toggleCategoryExpanded(cateToggle.dataset.cateToggle);
      renderCategoryPanel();
      return;
    }

    // Card-head tap toggles expansion like the app; the ⋯/arrow buttons inside
    // the head are handled above and never reach here.
    const cateHead = event.target.closest("#categoryList .cate-card-head");
    if (cateHead) {
      const card = cateHead.closest(".cate-card[data-cate-id]");
      if (card) {
        toggleCategoryExpanded(card.dataset.cateId);
        renderCategoryPanel();
      }
      return;
    }

    const typeButton = event.target.closest("button[data-bill-type]");
    if (typeButton) {
      clearBillForm(Number(typeButton.dataset.billType));
      return;
    }

    const assetBillItem = event.target.closest(".asset-bill-item[data-bill-id]");
    if (assetBillItem) {
      const bill = state.assetBills.find((item) => String(item.id) === assetBillItem.dataset.billId);
      if (bill) {
        try {
          await activateSubtab("bills");
          await fillBillForm(bill);
        } catch (error) {
          setStatus(String(error.message || error), false);
          setOutput(String(error.message || error));
        }
      }
      return;
    }

    const assetGroupHead = event.target.closest("#assetList .data-group-head[data-asset-group]");
    if (assetGroupHead) {
      const label = assetGroupHead.dataset.assetGroup;
      if (state.assetGroupsCollapsed.has(label)) state.assetGroupsCollapsed.delete(label);
      else state.assetGroupsCollapsed.add(label);
      renderAssets();
      return;
    }

    const assetRow = event.target.closest(".data-row.clickable[data-asset-id]");
    if (assetRow) {
      try { await openAssetDetail(assetRow.dataset.assetId); }
      catch (error) {
        setStatus(String(error.message || error), false);
        setOutput(String(error.message || error));
      }
      return;
    }

    const cardTypeChip = event.target.closest("#cardTypeChips button[data-card-type-filter]");
    if (cardTypeChip) {
      state.cardFilter.type = cardTypeChip.dataset.cardTypeFilter || "";
      renderCards();
      return;
    }

    const cardRow = event.target.closest("#cardList .data-row[data-card-id]");
    if (cardRow) {
      const card = cardById(cardRow.dataset.cardId);
      if (card) openCardOptions(card);
      return;
    }

    const billItem = event.target.closest(".bill-item[data-bill-id]");
    if (billItem) {
      if (state.batch.active && billItem.closest("#billList")) {
        if (swallowBillClickId && billItem.dataset.billId === swallowBillClickId) {
          swallowBillClickId = "";
          return;
        }
        toggleBatchBill(billItem.dataset.billId, billItem);
        return;
      }
      const bill = state.bills.find((item) => String(item.id) === billItem.dataset.billId);
      if (bill) openBillPreview(bill);
      return;
    }

    const dayHead = event.target.closest(".day-head[data-day-key]");
    if (dayHead && dayHead.closest("#billList") && !state.batch.active) {
      const key = dayHead.dataset.dayKey;
      if (key && key !== "unknown") {
        if (state.editingBill) clearBillForm();
        $("billDate").value = key;
        $("billMoney").focus();
      }
      return;
    }

    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const action = actions[button.dataset.action];
    if (!action) return;
    if (protectedActions.has(button.dataset.action) && !state.connected) {
      const message = "请先连接钱迹账号";
      setStatus(message, false);
      setOutput(message);
      syncConnectionState();
      return;
    }
    button.disabled = true;
    try { await action(button); }
    catch (error) {
      setStatus(String(error.message || error), false);
      setOutput(String(error.message || error));
    } finally {
      button.disabled = false;
      syncConnectionState();
    }
  });

  document.addEventListener("pointerdown", onSortPointerDown);
  document.addEventListener("pointermove", onSortPointerMove);
  document.addEventListener("pointerup", endSortDrag);
  document.addEventListener("pointercancel", endSortDrag);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (sortState.mode && !sortState.drag) {
      void exitSortMode(false);
      return;
    }
    if (state.batch.active) exitBatchMode();
  });

  // Long-press a bill row to enter batch selection (mirrors the app).
  document.addEventListener("pointerdown", (event) => {
    if (!state.connected || sortState.mode || state.batch.active) return;
    const item = event.target.closest("#billList .bill-item[data-bill-id]");
    if (!item) return;
    cancelBatchLongPress();
    batchLongPress = {
      x: event.clientX,
      y: event.clientY,
      timer: setTimeout(() => {
        batchLongPress = null;
        swallowBillClickId = String(item.dataset.billId);
        setTimeout(() => {
          swallowBillClickId = "";
        }, 700);
        enterBatchMode(item.dataset.billId);
      }, BATCH_LONG_PRESS_MS),
    };
  });
  document.addEventListener("pointermove", (event) => {
    if (batchLongPress && Math.hypot(event.clientX - batchLongPress.x, event.clientY - batchLongPress.y) > 10) cancelBatchLongPress();
  });
  document.addEventListener("pointerup", cancelBatchLongPress);
  document.addEventListener("pointercancel", cancelBatchLongPress);
  document.addEventListener("contextmenu", (event) => {
    if ((state.batch.active || batchLongPress) && event.target.closest("#billList .bill-item")) event.preventDefault();
  });

  setDefaultQueryRange();
  $("billDate").valueAsDate = new Date();
  setBillType(0, true);
  syncEditMode();
  const hasStoredCredentials = readStored();
  renderMcpConfig();
  syncConnectionState();
  if (hasStoredCredentials) autoConnect();
})();
