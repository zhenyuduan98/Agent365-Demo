"""
Agent 365 Demo Web Chat - Azure OpenAI 聊天机器人
MCP 集成版 + SQLite 历史记录持久化 + 对话记忆
"""

import asyncio
import os
import json
import logging
import sqlite3
import time
from pathlib import Path
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.environ["ENVIRONMENT"] = "Development"

from agent_framework import Agent, AgentSession, Message
from agent_framework.azure import AzureOpenAIChatClient
from azure.core.credentials import AzureKeyCredential

# MCP Tooling
from microsoft_agents_a365.tooling.extensions.agentframework.services.mcp_tool_registration_service import (
    McpToolRegistrationService,
)
from local_authentication_options import LocalAuthenticationOptions

SYSTEM_PROMPT = """You are a helpful AI assistant built with Microsoft Agent 365 framework.
You can respond in both English and Chinese. Be friendly and concise.
The user's name is 振宇 (Zhenyu). Use their name naturally where appropriate.
You are knowledgeable about Microsoft 365, Azure, and AI technologies.
You have access to MCP tools for Microsoft 365 (Mail, Calendar, Teams, Word, Excel, Planner)."""

# How many recent messages to include as context (user + bot pairs)
MAX_HISTORY_MESSAGES = 20

endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
api_version = os.getenv("AZURE_OPENAI_API_VERSION")
api_key = os.getenv("AZURE_OPENAI_API_KEY")

credential = AzureKeyCredential(api_key)
chat_client = AzureOpenAIChatClient(
    endpoint=endpoint,
    credential=credential,
    deployment_name=deployment,
    api_version=api_version,
)

agent = None
mcp_initialized = False
# Keep per-session AgentSession objects in memory for SDK conversation tracking
agent_sessions: dict[str, AgentSession] = {}

# --- SQLite ---
DB_PATH = Path(__file__).parent / "chat_history.db"

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp REAL NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON chat_history(session_id)")
    conn.commit()
    conn.close()
    logger.info(f"✅ SQLite DB: {DB_PATH}")

def save_msg(sid, role, content):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("INSERT INTO chat_history (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                 (sid, role, content, time.time()))
    conn.commit()
    conn.close()

def get_history(sid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT role, content, timestamp FROM chat_history WHERE session_id=? ORDER BY timestamp", (sid,)).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"], "timestamp": r["timestamp"]} for r in rows]

def get_sessions():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT session_id, MIN(timestamp) as first_msg, MAX(timestamp) as last_msg, COUNT(*) as msg_count,
        (SELECT content FROM chat_history c2 WHERE c2.session_id=c1.session_id AND c2.role='user' ORDER BY c2.timestamp LIMIT 1) as preview
        FROM chat_history c1 GROUP BY session_id ORDER BY last_msg DESC
    """).fetchall()
    conn.close()
    return [{"session_id":r["session_id"],"last_msg":r["last_msg"],"msg_count":r["msg_count"],"preview":(r["preview"] or "")[:50]} for r in rows]

def delete_session(sid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM chat_history WHERE session_id=?", (sid,))
    conn.commit()
    conn.close()

init_db()

async def create_agent():
    global agent, mcp_initialized
    agent = Agent(client=chat_client, instructions=SYSTEM_PROMPT)
    logger.info("✅ Agent created")

    # Setup MCP (set MCP_ENABLE=true in .env to activate)
    enable_mcp = os.getenv("MCP_ENABLE", "false").lower() == "true"
    if enable_mcp:
        try:
            bearer_token = os.getenv("MCP_BEARER_TOKEN", "")
            if bearer_token:
                tool_service = McpToolRegistrationService()
                try:
                    mcp_agent = await tool_service.add_tool_servers_to_agent(
                        chat_client=chat_client,
                        agent_instructions=SYSTEM_PROMPT,
                        initial_tools=[],
                        auth=None,
                        auth_handler_name=None,
                        auth_token=bearer_token,
                        turn_context=None,
                    )
                    if mcp_agent:
                        agent = mcp_agent
                        mcp_initialized = True
                        logger.info("✅ MCP servers connected!")
                    else:
                        raise RuntimeError("add_tool_servers_to_agent returned None")
                except TypeError as e:
                    logger.info(f"⚠️ SDK compat issue ({e}), building Agent manually with MCP tools")
                    mcp_tools = list(tool_service._connected_servers) if hasattr(tool_service, '_connected_servers') else []
                    if mcp_tools:
                        agent = Agent(client=chat_client, instructions=SYSTEM_PROMPT, tools=mcp_tools)
                        mcp_initialized = True
                        logger.info(f"✅ MCP connected manually with {len(mcp_tools)} tool(s)!")
                    else:
                        logger.warning("⚠️ No MCP tools found after setup")
            else:
                logger.warning("⚠️ MCP_BEARER_TOKEN not set, running chat-only mode")
        except Exception as e:
            logger.warning(f"⚠️ MCP setup failed: {e}, running chat-only mode")
            agent = Agent(client=chat_client, instructions=SYSTEM_PROMPT)
    else:
        logger.info("ℹ️ MCP disabled (set MCP_ENABLE=true in .env to activate)")

    return agent

# --- HTML ---
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agent 365 Chat Demo</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',-apple-system,BlinkMacSystemFont,sans-serif;background:#1a1a2e;color:#eee;height:100vh;display:flex;flex-direction:column}
.header{background:linear-gradient(135deg,#0078d4,#5c2d91);padding:16px 24px;display:flex;align-items:center;gap:12px;box-shadow:0 2px 8px rgba(0,0,0,.3);z-index:10}
.header .logo{font-size:28px}
.header h1{font-size:18px;font-weight:600}
.header .subtitle{font-size:12px;opacity:.8}
.header .badge{background:rgba(255,255,255,.15);padding:3px 10px;border-radius:12px;font-size:11px;margin-left:auto}
.main-layout{flex:1;display:flex;overflow:hidden}
.sidebar{width:280px;background:#16162a;border-right:1px solid #2d2d44;display:flex;flex-direction:column;transition:margin-left .3s}
.sidebar.collapsed{margin-left:-280px}
.sidebar-header{padding:12px 16px;border-bottom:1px solid #2d2d44;display:flex;align-items:center;justify-content:space-between}
.sidebar-header h3{font-size:14px;color:#9b87f5}
.new-chat-btn{padding:6px 14px;border-radius:16px;border:1px solid #9b87f5;background:transparent;color:#9b87f5;font-size:12px;cursor:pointer;transition:all .2s}
.new-chat-btn:hover{background:#9b87f5;color:#fff}
.session-list{flex:1;overflow-y:auto;padding:8px}
.session-item{padding:10px 12px;border-radius:8px;cursor:pointer;margin-bottom:4px;transition:background .2s}
.session-item:hover{background:#2d2d44}
.session-item.active{background:#2d2d5a;border-left:3px solid #9b87f5}
.session-item .preview{font-size:13px;color:#ccc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.session-item .meta{font-size:11px;color:#666;margin-top:3px;display:flex;justify-content:space-between;align-items:center}
.session-item .del{opacity:0;background:none;border:none;color:#ff6b6b;cursor:pointer;font-size:14px;padding:0 4px;transition:opacity .2s}
.session-item:hover .del{opacity:.7}
.session-item .del:hover{opacity:1}
.chat-area{flex:1;display:flex;flex-direction:column;min-width:0}
.toggle-sidebar{position:absolute;left:8px;top:72px;z-index:5;background:#2d2d44;border:none;color:#9b87f5;padding:6px 10px;border-radius:8px;cursor:pointer;font-size:16px;transition:background .2s}
.toggle-sidebar:hover{background:#3d3d5c}
.chat-container{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px}
.message{max-width:75%;padding:12px 16px;border-radius:16px;line-height:1.6;font-size:14px;white-space:pre-wrap;word-wrap:break-word;animation:fadeIn .3s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.message.user{align-self:flex-end;background:#0078d4;color:#fff;border-bottom-right-radius:4px}
.message.bot{align-self:flex-start;background:#2d2d44;color:#eee;border-bottom-left-radius:4px}
.message.bot .label{font-size:11px;color:#9b87f5;margin-bottom:4px;font-weight:600}
.typing{align-self:flex-start;padding:12px 16px;background:#2d2d44;border-radius:16px;display:none}
.typing span{display:inline-block;width:8px;height:8px;background:#9b87f5;border-radius:50%;margin:0 2px;animation:bounce 1.4s infinite ease-in-out}
.typing span:nth-child(2){animation-delay:.2s}
.typing span:nth-child(3){animation-delay:.4s}
@keyframes bounce{0%,80%,100%{transform:scale(0)}40%{transform:scale(1)}}
.input-area{padding:16px 20px;background:#16162a;display:flex;gap:12px;border-top:1px solid #2d2d44}
.input-area input{flex:1;padding:12px 16px;border-radius:24px;border:1px solid #3d3d5c;background:#1a1a2e;color:#eee;font-size:14px;outline:none;transition:border-color .2s}
.input-area input:focus{border-color:#0078d4}
.input-area input::placeholder{color:#666}
.input-area button{padding:12px 24px;border-radius:24px;border:none;background:linear-gradient(135deg,#0078d4,#5c2d91);color:#fff;font-size:14px;font-weight:600;cursor:pointer;transition:opacity .2s}
.input-area button:hover{opacity:.9}
.input-area button:disabled{opacity:.5;cursor:not-allowed}
.status-bar{padding:6px 24px;background:#16162a;font-size:11px;color:#666;text-align:center;border-top:1px solid #1d1d35}
.status-bar .dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:4px;background:#4ade80}
.empty-state{text-align:center;color:#555;padding:40px 20px;font-size:13px}
</style>
</head>
<body>
<div class="header">
 <div class="logo">🤖</div>
 <div><h1>Agent 365 Chat Demo</h1><div class="subtitle">Powered by Microsoft Agent Framework + Azure OpenAI</div></div>
 <div class="badge">🔧 MCP + Chat Mode</div>
</div>
<button class="toggle-sidebar" onclick="toggleSidebar()" title="Toggle history">☰</button>
<div class="main-layout">
 <div class="sidebar" id="sidebar">
  <div class="sidebar-header"><h3>💬 聊天记录</h3><button class="new-chat-btn" onclick="newChat()">+ 新对话</button></div>
  <div class="session-list" id="sessionList"><div class="empty-state">暂无历史记录</div></div>
 </div>
 <div class="chat-area">
  <div class="chat-container" id="chat"></div>
  <div class="typing" id="typing"><span></span><span></span><span></span></div>
  <div class="input-area">
   <input type="text" id="input" placeholder="输入消息..." autocomplete="off"/>
   <button id="send" onclick="sendMessage()">发送</button>
  </div>
  <div class="status-bar"><span class="dot"></span> Agent 运行中 · Azure OpenAI ($$DEPLOY$$)</div>
 </div>
</div>
<script>
const chat=document.getElementById('chat'),input=document.getElementById('input'),typing=document.getElementById('typing'),sendBtn=document.getElementById('send'),sessionList=document.getElementById('sessionList'),sidebar=document.getElementById('sidebar');
let sid=localStorage.getItem('a365_sid');
if(!sid){sid=genId();localStorage.setItem('a365_sid',sid)}
function genId(){return 's_'+Date.now().toString(36)+'_'+Math.random().toString(36).substr(2,6)}
function toggleSidebar(){sidebar.classList.toggle('collapsed')}
function newChat(){sid=genId();localStorage.setItem('a365_sid',sid);chat.innerHTML='';welcome();loadSessions();input.focus()}
function welcome(){addMsg("你好振宇！👋 我是 Agent 365 聊天助手。\n\n有什么可以帮你的？",'bot',false)}
async function loadHistory(id){
 try{const r=await fetch('/api/history?session_id='+encodeURIComponent(id));const d=await r.json();
 chat.innerHTML='';if(d.messages&&d.messages.length>0){for(const m of d.messages)addMsg(m.content,m.role,false)}else welcome();
 chat.scrollTop=chat.scrollHeight}catch(e){welcome()}}
async function loadSessions(){
 try{const r=await fetch('/api/sessions');const d=await r.json();
 if(d.sessions&&d.sessions.length>0){sessionList.innerHTML='';for(const s of d.sessions){
  const div=document.createElement('div');div.className='session-item'+(s.session_id===sid?' active':'');
  const t=new Date(s.last_msg*1000).toLocaleString('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
  div.innerHTML='<div class="preview">'+esc(s.preview||'新对话')+'</div><div class="meta"><span>'+t+' · '+s.msg_count+'条</span><button class="del" onclick="delSess(event,\''+s.session_id+'\')" title="删除">✕</button></div>';
  div.onclick=function(e){if(e.target.classList.contains('del'))return;switchSess(s.session_id)};sessionList.appendChild(div)}}
 else sessionList.innerHTML='<div class="empty-state">暂无历史记录</div>'}catch(e){}}
function switchSess(id){sid=id;localStorage.setItem('a365_sid',id);loadHistory(id);loadSessions()}
async function delSess(e,id){e.stopPropagation();if(!confirm('确定删除？'))return;
 await fetch('/api/sessions/'+encodeURIComponent(id),{method:'DELETE'});if(id===sid)newChat();else loadSessions()}
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage()}});
async function sendMessage(){
 const t=input.value.trim();if(!t)return;addMsg(t,'user',true);input.value='';sendBtn.disabled=true;typing.style.display='block';chat.scrollTop=chat.scrollHeight;
 try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:t,session_id:sid})});
 const d=await r.json();addMsg(d.reply,'bot',true);loadSessions()}
 catch(err){addMsg('⚠️ 连接错误: '+err.message,'bot',true)}
 finally{typing.style.display='none';sendBtn.disabled=false;input.focus()}}
function addMsg(text,role,anim){
 const d=document.createElement('div');d.className='message '+role;if(!anim)d.style.animation='none';
 if(role==='bot')d.innerHTML='<div class="label">🤖 Agent 365</div>'+esc(text);else d.textContent=text;
 chat.appendChild(d);chat.scrollTop=chat.scrollHeight}
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
loadHistory(sid);loadSessions();input.focus();
</script>
</body>
</html>""".replace("$$DEPLOY$$", deployment or "gpt-5.2-chat")


async def handle_chat(request):
    global agent
    try:
        data = await request.json()
        msg = data.get("message", "").strip()
        sid = data.get("session_id", "default")
        if not msg:
            return web.json_response({"reply": "请输入消息"})
        logger.info(f"📨 [{sid[:12]}] {msg}")
        save_msg(sid, "user", msg)
        if agent is None:
            await create_agent()

        # Build conversation history as Message objects
        history = get_history(sid)
        messages = []
        # Include up to MAX_HISTORY_MESSAGES recent messages (excluding the current one we just saved)
        recent = history[-(MAX_HISTORY_MESSAGES + 1):-1] if len(history) > 1 else []
        for h in recent:
            role = "user" if h["role"] == "user" else "assistant"
            messages.append(Message(role=role, text=h["content"]))
        # Add current user message
        messages.append(Message(role="user", text=msg))

        # Get or create AgentSession for this conversation
        if sid not in agent_sessions:
            agent_sessions[sid] = AgentSession(session_id=sid)

        result = await agent.run(messages, session=agent_sessions[sid])
        reply = ""
        for attr in ("contents", "text", "content"):
            if hasattr(result, attr):
                reply = str(getattr(result, attr))
                break
        if not reply:
            reply = str(result)
        logger.info(f"🤖 [{sid[:12]}] {reply[:100]}...")
        save_msg(sid, "bot", reply)
        return web.json_response({"reply": reply, "mcp_enabled": mcp_initialized})
    except Exception as e:
        logger.error(f"❌ {e}", exc_info=True)
        return web.json_response({"reply": f"出错了: {e}"}, status=500)

async def handle_index(request):
    return web.Response(text=HTML_PAGE, content_type="text/html")

async def handle_health(request):
    return web.json_response({"status": "ok", "agent": "Agent365 Chat Demo", "model": deployment, "mcp_enabled": mcp_initialized})

async def handle_history(request):
    sid = request.query.get("session_id", "default")
    return web.json_response({"session_id": sid, "messages": get_history(sid)})

async def handle_sessions(request):
    return web.json_response({"sessions": get_sessions()})

async def handle_delete(request):
    sid = request.match_info.get("session_id", "")
    if sid:
        delete_session(sid)
        agent_sessions.pop(sid, None)
    return web.json_response({"status": "ok"})

app = web.Application()
app.router.add_get("/", handle_index)
app.router.add_post("/api/chat", handle_chat)
app.router.add_get("/api/health", handle_health)
app.router.add_get("/api/history", handle_history)
app.router.add_get("/api/sessions", handle_sessions)
app.router.add_delete("/api/sessions/{session_id}", handle_delete)

async def on_startup(app):
    await create_agent()

app.on_startup.append(on_startup)

if __name__ == "__main__":
    port = 3979
    print("=" * 50)
    print("🤖 Agent 365 Chat Demo (MCP + Memory)")
    print(f"🚀 http://localhost:{port}")
    print(f"📡 Azure OpenAI: {endpoint}")
    print(f"🧠 Model: {deployment}")
    print(f"💾 History: {DB_PATH}")
    print(f"🔧 MCP: ToolingManifest.json")
    print("=" * 50)
    web.run_app(app, host="0.0.0.0", port=port)
