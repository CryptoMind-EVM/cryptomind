#!/usr/bin/env node
// 量測用 LLM 代理：App → 這支 → llama-server。每個 /v1/chat/completions 記一行 JSON（prompt 組成、首字、總時間）。
//
// 為什麼有這支（2026-10-06）：聊天一題 30 秒裡 25 秒是第一次主呼叫讀 prompt，但從 app 這邊看不出「prompt 裡到底
// 有什麼、讀了多久」。這支代理只轉送、不改內容，記下每次呼叫的 system／工具／user 字數、工具個數、TTFB、總時間，
// 並把第一個帶工具的請求原文存成 <log>.dump.json，方便重放與拆解（見 docs/plans/2026-10-06-chat-latency-domain-gate.md）。
//
// 用法（本機）：
//   node scripts/measure_llm_calls.cjs llm_calls.jsonl            # 預設 :8081 → 127.0.0.1:8080
//   LISTEN_PORT=8081 UPSTREAM_PORT=8080 node scripts/measure_llm_calls.cjs out.jsonl
//   然後 app 端設 LOCAL_LLAMA_BASE_URL=http://127.0.0.1:8081/v1
//
// 只綁 127.0.0.1；log 內只有字數統計與最後一句使用者訊息的前 60 字（本機除錯用，不要拿去正式環境）。
const http = require('http');
const fs = require('fs');

const LOG = process.argv[2];
if (!LOG) {
  console.error('usage: node scripts/measure_llm_calls.cjs <log.jsonl>');
  process.exit(2);
}
const LISTEN_PORT = Number(process.env.LISTEN_PORT || 8081);
const UPSTREAM = { host: '127.0.0.1', port: Number(process.env.UPSTREAM_PORT || 8080) };
let seq = 0;

const charsOf = (m) => (typeof m.content === 'string' ? m.content.length : JSON.stringify(m.content || '').length);
const sumChars = (msgs, role) => msgs.filter((m) => m.role === role).reduce((s, m) => s + charsOf(m), 0);

function describe(body, t0) {
  const j = JSON.parse(body.toString('utf8'));
  const msgs = j.messages || [];
  const tools = j.tools || [];
  if (tools.length && !fs.existsSync(LOG + '.dump.json')) fs.writeFileSync(LOG + '.dump.json', body);
  const lastUser = msgs.filter((m) => m.role === 'user').pop();
  // 上一輪 assistant 呼叫了哪些工具（看 agent 在迴圈裡做什麼、有沒有呼叫 request_more_tools）
  const lastAsst = msgs.filter((m) => m.role === 'assistant' && Array.isArray(m.tool_calls) && m.tool_calls.length).pop();
  const called = lastAsst ? lastAsst.tool_calls.map((tc) => (tc.function && tc.function.name) || '?').join(',') : '';
  return {
    n: ++seq,
    t_start: t0,
    stream: !!j.stream,
    max_tokens: j.max_tokens || j.max_completion_tokens || null,
    tool_choice: j.tool_choice || null,
    n_messages: msgs.length,
    sys_chars: sumChars(msgs, 'system'),
    user_chars: sumChars(msgs, 'user'),
    tool_msg_chars: sumChars(msgs, 'tool'),
    asst_chars: sumChars(msgs, 'assistant'),
    n_tools: tools.length,
    tools_chars: JSON.stringify(tools).length,
    last_user: lastUser && lastUser.content ? String(lastUser.content).slice(0, 60) : '',
    called,
  };
}

http
  .createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const body = Buffer.concat(chunks);
      const t0 = Date.now();
      let info = null;
      if (req.url.includes('/chat/completions')) {
        try {
          info = describe(body, t0);
        } catch (e) {
          info = { n: ++seq, parse_error: String(e) };
        }
      }
      const up = http.request(
        { ...UPSTREAM, path: req.url, method: req.method, headers: { ...req.headers, host: `127.0.0.1:${UPSTREAM.port}` } },
        (ur) => {
          let first = 0;
          let bytes = 0;
          let tail = '';
          res.writeHead(ur.statusCode, ur.headers);
          ur.on('data', (c) => {
            if (!first) first = Date.now();
            bytes += c.length;
            tail = (tail + c.toString('utf8')).slice(-1500);
            res.write(c);
          });
          ur.on('end', () => {
            res.end();
            if (!info) return;
            info.ttfb_ms = first ? first - t0 : null;
            info.total_ms = Date.now() - t0;
            info.resp_bytes = bytes;
            info.status = ur.statusCode;
            const completion = tail.match(/"completion_tokens":\s*(\d+)/);
            const prompt = tail.match(/"prompt_tokens":\s*(\d+)/);
            if (completion) info.completion_tokens = Number(completion[1]);
            if (prompt) info.prompt_tokens = Number(prompt[1]);
            fs.appendFileSync(LOG, JSON.stringify(info) + '\n');
          });
        }
      );
      up.on('error', (e) => {
        res.writeHead(502);
        res.end(String(e));
      });
      up.end(body);
    });
  })
  .listen(LISTEN_PORT, '127.0.0.1', () => console.log(`measure proxy :${LISTEN_PORT} -> :${UPSTREAM.port}, log ${LOG}`));
