import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Beaker,
  Bot,
  Box,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock3,
  Code2,
  Copy,
  Download,
  ExternalLink,
  File,
  FileCode2,
  FileInput,
  FileOutput,
  FileText,
  GitBranch,
  Globe2,
  Layers3,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Rocket,
  Settings2,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Upload,
  Waypoints,
  X,
  XCircle,
  Zap,
} from "lucide-react";
import "./styles.css";

const ACTIVE_RUN = ["queued", "running", "pending"];
const ACTIVE_BATCH = [
  "queued",
  "analyzing",
  "proposing",
  "evaluating",
  "confirming",
  "running",
];
const TABS = [
  { id: "build", label: "Build", icon: Box },
  { id: "runs", label: "Runs & traces", icon: Activity },
  { id: "evals", label: "Evals", icon: Beaker },
  { id: "improve", label: "Improve", icon: Sparkles },
  { id: "deploy", label: "Deploy", icon: Globe2 },
];
const DEMO_INSTRUCTIONS =
  "You are a careful data transformation agent. Read the provided input files, write and execute code to complete the task, and save the requested output file. Inspect your result before finishing. Explain what you changed and link to the resulting file.";

async function api(path, options = {}) {
  const response = await fetch("/api" + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...(options.body !== undefined
      ? { body: JSON.stringify(options.body) }
      : {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(
            data.detail || data.error || `Request failed (${response.status})`,
          ),
    );
  return data;
}
const post = (path, body = {}) => api(path, { method: "POST", body });
const cls = (...values) => values.filter(Boolean).join(" ");
const shortId = (value) => (value ? String(value).slice(0, 8) : "—");
const time = (value) =>
  value
    ? new Date(value).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "—";
const date = (value) =>
  value
    ? new Date(value).toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "—";
const pct = (value) => (value == null ? "—" : `${Math.round(value * 100)}%`);
const num = (value) => (value == null ? "—" : Number(value).toFixed(3));
const money = (value) =>
  value == null
    ? "—"
    : value === 0
      ? "$0.00"
      : "$" + Number(value).toFixed(value < 0.01 ? 4 : 3);
const duration = (value) =>
  value == null
    ? "—"
    : value < 1000
      ? Math.round(value) + " ms"
      : (value / 1000).toFixed(1) + "s";
const versionLabel = (version) =>
  version
    ? `v${version.number ?? version.version ?? version.version_number ?? shortId(version.id || version)}`
    : "No version";
const prettify = (value) =>
  String(value || "")
    .replaceAll("_", " ")
    .replaceAll(".", " · ");
const json = (value) =>
  typeof value === "string" ? value : JSON.stringify(value, null, 2);
function usePolling(fn, delay = 2000) {
  const callback = useRef(fn);
  callback.current = fn;
  useEffect(() => {
    const timer = setInterval(() => callback.current(), delay);
    return () => clearInterval(timer);
  }, [delay]);
}
function Badge({ status, children }) {
  return (
    <span className={cls("badge", status && `badge-${status}`)}>
      {status && <span className="status-dot" />}
      {children || prettify(status)}
    </span>
  );
}
function Button({ children, icon: Icon, busy, className, ...props }) {
  return (
    <button
      className={cls("button", className)}
      {...props}
      disabled={props.disabled || busy}
    >
      {busy ? (
        <Loader2 size={15} className="spin" />
      ) : Icon ? (
        <Icon size={15} />
      ) : null}
      {children}
    </button>
  );
}
function Empty({ icon: Icon = Layers3, title, children, action }) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon size={24} strokeWidth={1.4} />
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}
function Spinner({ label = "Loading workspace" }) {
  return (
    <div className="loading">
      <Loader2 className="spin" size={18} />
      {label}
    </div>
  );
}
function Field({ label, hint, children }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}
function Modal({ title, subtitle, children, onClose, wide }) {
  useEffect(() => {
    const cb = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", cb);
    return () => document.removeEventListener("keydown", cb);
  }, [onClose]);
  return (
    <div
      className="modal-shade"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <section
        className={cls("modal", wide && "modal-wide")}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-heading">
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button className="icon-button" aria-label="Close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}
function CopyButton({ text, label = "Copy", className }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      className={className || "button-quiet"}
      icon={copied ? Check : Copy}
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1800);
      }}
    >
      {copied ? "Copied" : label}
    </Button>
  );
}

function ArtifactFile({ file, token = "" }) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  const isCode = /\.(py|js|jsx|ts|tsx|sh|sql|html|css|rs|go|java|c|cpp)$/i.test(file.name);
  async function toggle() {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (preview) return;
    setBusy(true);
    setError("");
    try { setPreview(await api(`/artifacts/${file.id}/preview${query}`)); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  return <section className="artifact-entry" aria-label={`File ${file.name}`}>
    <div className="artifact-file-header">
      <FileCode2 size={15}/><strong title={file.name}>{file.name}</strong>
      <div className="artifact-actions">
        <button type="button" className="text-button" aria-label={`${open ? "Hide" : "View"} ${file.name}`} aria-expanded={open} onClick={toggle}>
          <Code2 size={13}/>{open ? "Hide" : isCode ? "View code" : "Preview"}
        </button>
        <a href={file.url + query} target="_blank" rel="noreferrer" aria-label={`Download ${file.name}`}><Download size={13}/>Download</a>
      </div>
    </div>
    {open && <div className="artifact-preview">
      {busy && <Spinner label="Loading file"/>}
      {error && <p className="inline-error" role="alert">{error}</p>}
      {preview && <>
        <div className="artifact-preview-toolbar"><span>{preview.truncated ? "Preview limited to 128 KB · download for full file" : "Saved file snapshot"}</span><CopyButton text={preview.content} label="Copy code"/></div>
        <div className="code-preview-scroll" tabIndex={0} aria-label={`Contents of ${file.name}`}>
          <pre className="code-line-numbers" aria-hidden="true">{preview.content.split("\n").map((_, i) => i + 1).join("\n")}</pre>
          <pre className="code-preview-content"><code>{preview.content}</code></pre>
        </div>
      </>}
    </div>}
  </section>;
}

function FilePicker({ files, onChange }) {
  const [error, setError] = useState("");
  async function pick(list) {
    try {
      const added = await Promise.all(
        Array.from(list).map(async (file) => {
          if (file.size > 2 * 1024 * 1024)
            throw new Error(`${file.name} exceeds 2 MB`);
          return { name: file.name, content: await file.text() };
        }),
      );
      onChange([
        ...files.filter((old) => !added.some((item) => item.name === old.name)),
        ...added,
      ]);
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }
  return (
    <div className="file-picker">
      <div
        className="file-drop"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          pick(e.dataTransfer.files);
        }}
      >
        <Upload size={16} />
        <span>
          Drop input files here or{" "}
          <label className="inline-link">
            browse
            <input
              type="file"
              multiple
              onChange={(e) => {
                pick(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
        </span>
        <span className="muted file-limit">Text files · up to 2 MB each</span>
      </div>
      {files.length > 0 && (
        <div className="file-chips">
          {files.map((file, i) => (
            <span className="file-chip" key={file.name}>
              <FileText size={13} />
              {file.name}
              <button
                aria-label={`Remove ${file.name}`}
                onClick={() =>
                  onChange(files.filter((_, index) => index !== i))
                }
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      {error && <p className="form-error">{error}</p>}
    </div>
  );
}
function RunComposer({
  onRun,
  busy,
  compact,
  disabled,
  defaultPrompt = "",
  defaultFiles = [],
}) {
  const [prompt, setPrompt] = useState(defaultPrompt);
  const [files, setFiles] = useState(defaultFiles);
  return (
    <div className={cls("run-composer", compact && "compact")}>
      <div className="section-heading">
        <div className="heading-icon">
          <Play size={16} />
          <h3>Try your agent</h3>
        </div>
        <span className="muted">Fresh workspace each run</span>
      </div>
      <textarea
        aria-label="Task for the agent"
        rows={compact ? 3 : 4}
        placeholder="What should this agent do? Be specific about the output you expect."
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
      />
      <FilePicker files={files} onChange={setFiles} />
      <div className="composer-footer">
        <span>
          <ShieldCheck size={13} />
          Isolated code & file tools
        </span>
        <Button
          className="button-primary"
          icon={Play}
          busy={busy}
          disabled={disabled || !prompt.trim()}
          onClick={() => onRun({ prompt, files })}
        >
          Run agent<kbd>↵</kbd>
        </Button>
      </div>
    </div>
  );
}
function AgentChat({ agentId, slug, token = "", disabled = false, onError, onRefresh, latestVersionId }) {
  const prefix = slug ? `/deployments/${slug}` : `/agents/${agentId}`;
  const storageKey = `brainbase-chat:${slug ? "deployment:" + slug : "agent:" + agentId}`;
  const [sessionId, setSessionId] = useState(() => {
    try { return localStorage.getItem(storageKey) || ""; } catch { return ""; }
  });
  const selected = useRef(sessionId);
  const loadSequence = useRef(0);
  const [session, setSession] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [prompt, setPrompt] = useState("");
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(!!sessionId);
  const [inspected, setInspected] = useState(null);
  const [chatError, setChatError] = useState("");
  const transcript = useRef(null);
  const active = !!session?.active_run_id;
  const runs = session?.runs || [];
  function choose(id) {
    selected.current = id;
    setSessionId(id);
    setSession(null);
    setLoading(!!id);
    setInspected(null);
    setChatError("");
    setPrompt("");
    setFiles([]);
    try { id ? localStorage.setItem(storageKey, id) : localStorage.removeItem(storageKey); } catch {}
  }
  const refreshList = useCallback(async () => {
    if (!slug) setSessions(await api(`${prefix}/sessions`));
  }, [prefix, slug]);
  const load = useCallback(async (id) => {
    const sequence = ++loadSequence.current;
    const value = await api(`${prefix}/sessions/${id}${token ? `?token=${encodeURIComponent(token)}` : ""}`);
    if (selected.current === id && sequence === loadSequence.current) setSession(value);
    return value;
  }, [prefix, token]);
  useEffect(() => {
    let alive = true;
    setLoading(!!sessionId);
    if (sessionId) load(sessionId).catch((e) => {
      if (alive && selected.current === sessionId) {
        setChatError(`Could not restore this chat: ${e.message}`);
      }
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [sessionId, load]);
  useEffect(() => { refreshList().catch((e) => onError(e.message)); }, [refreshList]);
  usePolling(async () => {
    if (!sessionId || !active) return;
    try {
      const value = await load(sessionId);
      if (!value.active_run_id) { await refreshList(); onRefresh?.(); }
    } catch (e) { setChatError(e.message); }
  }, 1500);
  useEffect(() => {
    if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [sessionId, runs.length]);
  async function send(e) {
    e.preventDefault();
    if (!prompt.trim() || busy || active || disabled || loading) return;
    setBusy(true);
    setChatError("");
    try {
      let id = sessionId;
      if (!id) {
        const created = await post(`${prefix}/sessions`, token ? { token } : {});
        id = created.id;
        selected.current = id;
        setSessionId(id);
        setSession(created);
        try { localStorage.setItem(storageKey, id); } catch {}
      }
      await post(`${prefix}/sessions/${id}/runs`, { prompt, files, ...(token ? { token } : {}) });
      setPrompt("");
      setFiles([]);
      await load(id);
      await refreshList();
      onRefresh?.();
    } catch (e) { setChatError(e.message); }
    finally { setBusy(false); }
  }
  return (
    <section className="card agent-chat">
      <div className="card-header chat-header">
        <div className="heading-icon"><MessageSquare size={17}/><h3>Chat with your agent</h3></div>
        <Button className="button-small" icon={Plus} disabled={busy || active} onClick={() => choose("")}>New chat</Button>
      </div>
      <div className="chat-toolbar">
        {!slug && sessions.length > 0 && <select aria-label="Conversation history" value={sessionId} disabled={busy || active} onChange={(e) => choose(e.target.value)}>
          <option value="">New conversation</option>
          {sessions.map((s) => <option key={s.id} value={s.id}>{s.title} · v{s.version_number}{s.deployment_id ? " · shared" : ""}</option>)}
        </select>}
        <span className="tiny-copy">{session ? `Pinned to v${session.version_number} · ${session.model}` : "New chats use the latest version"}</span>
        {session && latestVersionId && session.version_id !== latestVersionId && <p className="chat-version-note">A newer agent version is available. Start a new chat to use it.</p>}
      </div>
      <div className="chat-transcript" ref={transcript} aria-label="Conversation messages" aria-live="polite">
        {loading && <Spinner label="Loading conversation"/>}
        {!loading && !runs.length && <div className="chat-empty"><Bot size={25}/><strong>Build something together.</strong><p>Ask for a script, then refine it. Your messages and workspace files carry forward in this chat.</p></div>}
        {runs.map((r) => <div className="chat-turn" key={r.id}>
          <article className="chat-message chat-user"><div className="chat-speaker">You · turn {r.turn_index}</div><div className="chat-message-text">{r.prompt}</div>{r.input_names?.length > 0 && <div className="tiny-copy">Attached: {r.input_names.join(", ")}</div>}</article>
          <article className="chat-message chat-assistant"><div className="chat-speaker"><Bot size={13}/>Agent<Badge status={r.status}/></div>
            {r.output ? <div className="chat-message-text">{r.output}</div> : ACTIVE_RUN.includes(r.status) ? <p className="chat-working"><Loader2 size={14} className="spin"/>Working in your conversation’s workspace…</p> : <p className="inline-error">{r.error || "This turn did not return a reply."}</p>}
            {r.artifacts?.length > 0 && <div className="artifact-files">{r.artifacts.map((f) => <ArtifactFile key={f.id} file={f} token={token}/>)}</div>}
            <button className="text-button chat-trace" onClick={() => setInspected(inspected === r.id ? null : r.id)}><Activity size={13}/>{inspected === r.id ? "Hide trace" : "View trace"} · {duration(r.latency_ms)} · {money(r.cost_usd)}</button>
          </article>
        </div>)}
      </div>
      {chatError && <div className="inline-error" role="alert">{chatError}</div>}
      <form className="chat-composer" onSubmit={send}>
        <textarea aria-label="Message to agent" placeholder={runs.length ? "Ask a follow-up, such as ‘add a CLI to that script’…" : "What would you like to build?"} rows={3} value={prompt} disabled={busy || active || disabled || loading} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) send(e); }}/>
        <FilePicker files={files} onChange={setFiles}/>
        <div className="composer-footer"><span><ShieldCheck size={13}/>Files persist in this chat · fresh container each turn</span><Button type="submit" className="button-primary" busy={busy || active} disabled={!prompt.trim() || disabled || loading || busy || active} icon={ArrowUpRight}>Send message</Button></div>
      </form>
      {inspected && <div className="chat-inspector"><RunDetail key={inspected} runId={inspected} token={token} shared={!!slug} onError={onError}/></div>}
    </section>
  );
}

function CreateAgentModal({
  models,
  defaultModel,
  onClose,
  onCreated,
  onError,
}) {
  const [form, setForm] = useState({
    name: "",
    description: "",
    instructions: DEMO_INSTRUCTIONS,
    model: defaultModel || models[0]?.id || "",
    generate_evals: 8,
    auto_improve: true,
  });
  const [busy, setBusy] = useState(false);
  const set = (key, value) => setForm({ ...form, [key]: value });
  async function create(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const agent = await post("/agents", form);
      onCreated(agent);
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Create an agent"
      subtitle="Give it a purpose. You can refine its behavior as you go."
      onClose={onClose}
    >
      <form onSubmit={create}>
        <Field label="Agent name">
          <input
            autoFocus
            placeholder="e.g. Customer data assistant"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            required
          />
        </Field>
        <Field label="Description">
          <input
            placeholder="A short description for your team"
            value={form.description}
            onChange={(e) => set("description", e.target.value)}
          />
        </Field>
        <Field
          label="Instructions"
          hint="Describe the agent’s job, constraints, and expected outputs."
        >
          <textarea
            rows={7}
            value={form.instructions}
            onChange={(e) => set("instructions", e.target.value)}
            required
          />
        </Field>
        <Field
          label="Baseline model"
          hint="Any tool-capable OpenRouter model. Hill climbing may switch models and tune the turn limit from measured results."
        >
          <ModelPicker
            models={models}
            value={form.model}
            onChange={(value) => set("model", value)}
          />
        </Field>
        <label className="automation-row">
          <span>
            Generate 8 starter evals
            <small>
              A strong model drafts cases with hidden tests, keeps only those
              whose reference solution passes, then starts climbing
              automatically. Use Evals to add a larger suite later.
            </small>
          </span>
          <input
            type="checkbox"
            checked={form.generate_evals > 0}
            onChange={(e) =>
              setForm({
                ...form,
                generate_evals: e.target.checked ? 8 : 0,
                auto_improve: e.target.checked,
              })
            }
          />
        </label>
        <div className="modal-footer">
          <Button type="button" className="button-quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button
            className="button-primary"
            icon={Plus}
            busy={busy}
            disabled={
              !form.name.trim() ||
              !form.instructions.trim() ||
              (models.length > 0 && !models.some((m) => m.id === form.model))
            }
          >
            Create agent
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function ModelPicker({ models, value, onChange }) {
  const selected = models.find((m) => m.id === value);
  const price = (m) =>
    m.input_per_mtok != null ? ` · $${m.input_per_mtok} / $${m.output_per_mtok} per M tokens` : "";
  return (
    <div className="model-picker">
      <input
        list="openrouter-models"
        value={value}
        placeholder="Search OpenRouter models, e.g. anthropic/ or qwen/"
        onChange={(e) => onChange(e.target.value.trim())}
        spellCheck="false"
      />
      <datalist id="openrouter-models">
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.id + price(m)}
          </option>
        ))}
      </datalist>
      <small className={selected || !value ? "muted" : "text-red"}>
        {selected
          ? `${price(selected).replace(" · ", "") || "Price unknown"}${selected.context_length ? ` · ${Math.round(selected.context_length / 1000)}K context` : ""}`
          : value
            ? "Not a tool-capable OpenRouter model"
            : `${models.length} models available`}
      </small>
    </div>
  );
}
function ModelSelect({ models, value, onChange }) {
  return models.length ? (
    <div className="select-wrap">
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {models.map((model) => (
          <option key={model.id} value={model.id}>
            {model.name || model.id}
          </option>
        ))}
      </select>
      <ChevronDown size={14} />
    </div>
  ) : (
    <input
      value={value}
      placeholder="Configure a model in the server environment"
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function BuildView({
  agent,
  models,
  status,
  onRefresh,
  onRun,
  onError,
  running,
}) {
  const version = agent.current_version || agent.versions?.at(-1) || {};
  const [instructions, setInstructions] = useState(version.instructions || "");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    setInstructions(version.instructions || "");
  }, [agent.id, version.id]);
  // Model and turn limit are owned by the learner; users edit instructions only.
  const changed = instructions !== (version.instructions || "");
  async function save() {
    setSaving(true);
    try {
      await post(`/agents/${agent.id}/versions`, {
        instructions,
        playbook: version.playbook || "",
      });
      await onRefresh();
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="build-grid">
      <div className="build-main">
        <AgentChat agentId={agent.id} disabled={changed} onError={onError} onRefresh={onRefresh} latestVersionId={version.id}/>
        <section className="card instruction-card">
          <div className="card-header">
            <div className="heading-icon">
              <FileCode2 size={17} />
              <h3>Agent instructions</h3>
            </div>
            <Badge>{versionLabel(version)}</Badge>
          </div>
          <p className="card-intro">
            Define how this agent approaches a task. Every saved change becomes
            an immutable version.
          </p>
          <textarea
            className="instruction-editor"
            aria-label="Agent instructions"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            spellCheck="false"
          />
          <div className="editor-footer">
            <span>
              <GitBranch size={13} />
              {changed ? "Unsaved changes" : "All changes saved"}
            </span>
            <Button
              className={changed ? "button-primary" : "button-small"}
              icon={Check}
              busy={saving}
              disabled={!changed || !instructions.trim()}
              onClick={save}
            >
              Save version
            </Button>
          </div>
        </section>
        <details className="one-shot-test"><summary>One-shot test for evaluation feedback</summary>
          <RunComposer onRun={onRun} busy={running} disabled={changed} defaultPrompt={agent.demo_prompt || ""} defaultFiles={agent.demo_files || []}/>
        </details>
        {changed && (
          <p className="subtle-note">
            Save your changes before running this version.
          </p>
        )}
      </div>
      <aside className="build-aside">
        <section className="card">
          <div className="card-header">
            <div className="heading-icon">
              <Settings2 size={16} />
              <h3>Configuration</h3>
            </div>
          </div>
          <div className="card-body">
            <div className="mini-definition">
              <span>Model</span>
              <strong title="Chosen by the learner during hill climbing">{version.model || "—"}</strong>
            </div>
            <div className="mini-definition">
              <span>Turn limit</span>
              <strong title="Chosen by the learner during hill climbing">{version.max_turns ?? "—"}</strong>
            </div>
            <p className="tiny-copy">Model and turn limit are tuned by the learner from eval results.</p>
            <div className="mini-definition">
              <span>Runtime</span>
              <strong>OpenAI Agents SDK</strong>
            </div>
            <div className="mini-definition">
              <span>Workspace</span>
              <strong>
                {(typeof status.sandbox === "object"
                  ? status.sandbox?.kind || "Docker"
                  : status.sandbox) || "Not configured"}
              </strong>
            </div>
            <div className="mini-definition">
              <span>Version</span>
              <strong>{versionLabel(version)}</strong>
            </div>
          </div>
        </section>
        <section className="card">
          <div className="card-header">
            <div className="heading-icon">
              <SquareTerminal size={16} />
              <h3>Available tools</h3>
            </div>
            <Badge>4 tools</Badge>
          </div>
          <div className="tool-list">
            {[
              {
                name: "bash",
                text: "Run code and shell commands",
                icon: SquareTerminal,
              },
              {
                name: "read_file",
                text: "Inspect files in the workspace",
                icon: FileInput,
              },
              {
                name: "write_file",
                text: "Create code and output files",
                icon: FileOutput,
              },
              {
                name: "list_files",
                text: "Explore workspace contents",
                icon: Layers3,
              },
            ].map(({ name, text, icon: Icon }) => (
              <div className="tool-item" key={name}>
                <Icon size={17} />
                <div>
                  <code>{name}</code>
                  <span>{text}</span>
                </div>
                <span className="enabled-dot" />
              </div>
            ))}
          </div>
        </section>
        <section className="card playbook-card">
          <div className="card-header">
            <div className="heading-icon">
              <Sparkles size={16} />
              <h3>Learned playbook</h3>
            </div>
          </div>
          {version.playbook ? (
            <pre className="playbook-text">{version.playbook}</pre>
          ) : (
            <div className="playbook-empty">
              <div className="playbook-orbit">
                <Sparkles size={23} />
              </div>
              <p>Good agents get better.</p>
              <span>
                Corrections become tested playbook changes. Your first lesson
                starts with a run.
              </span>
            </div>
          )}
        </section>
      </aside>
    </div>
  );
}

function TraceEvent({ event, index }) {
  const [open, setOpen] = useState(false);
  const payload = event.payload || {};
  const type = event.type || "event";
  const isTool = type.includes("tool");
  const isModel = type.includes("model");
  const isError =
    type.includes("error") ||
    payload.error ||
    payload.status === "error" ||
    payload.result?.error ||
    payload.exit_code > 0 ||
    payload.result?.exit_code > 0;
  const isArtifact = type.includes("artifact");
  const Icon = isError
    ? XCircle
    : isTool
      ? SquareTerminal
      : isModel
        ? Bot
        : isArtifact
          ? FileOutput
          : type.includes("feedback")
            ? MessageSquare
            : Activity;
  const name = payload.tool || payload.tool_name || payload.name;
  const summary =
    isTool && name
      ? name
      : isModel
        ? payload.model || "Model inference"
        : prettify(type);
  const elapsed = payload.duration_ms ?? payload.latency_ms;
  const keys = Object.keys(payload);
  return (
    <div
      className={cls(
        "trace-event",
        isError && "trace-error",
        open && "trace-expanded",
      )}
    >
      <button
        className="trace-event-button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span
          className={cls(
            "trace-node",
            isTool && "trace-node-tool",
            isModel && "trace-node-model",
          )}
        >
          <Icon size={14} />
        </span>
        <span className="trace-info">
          <span className="trace-event-title">
            {summary}
            <span className="event-phase">
              {isTool || isModel ? type.split(/[._]/).at(-1) : ""}
            </span>
          </span>
          <span className="trace-event-subtitle">
            {payload.summary ||
              (payload.arguments
                ? json(payload.arguments).replaceAll("\n", " ").slice(0, 110)
                : payload.error
                  ? String(payload.error).slice(0, 110)
                  : isModel
                    ? payload.response
                      ? String(payload.response).slice(0, 110)
                      : "Request and response metadata"
                    : `Event ${event.seq ?? index + 1}`)}
          </span>
        </span>
        <span className="trace-event-time">
          {elapsed != null
            ? duration(elapsed)
            : time(event.ts || event.created_at)}
        </span>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open && (
        <div className="trace-event-details">
          {keys.length ? (
            keys.map((key) => (
              <div className="trace-payload" key={key}>
                <span>{prettify(key)}</span>
                <pre>{json(payload[key])}</pre>
              </div>
            ))
          ) : (
            <span className="muted">No additional event data.</span>
          )}
        </div>
      )}
    </div>
  );
}
function FeedbackForm({ run, onSaved, onError, demo }) {
  const [rating, setRating] = useState(run.feedback?.rating || "negative");
  const [correction, setCorrection] = useState(run.feedback?.correction || "");
  const [expectedName, setExpectedName] = useState(
    demo ? "clean.csv" : "output.csv",
  );
  const [expectedContent, setExpectedContent] = useState("");
  const [testCode, setTestCode] = useState("");
  const [checkType, setCheckType] = useState("tests");
  const [busy, setBusy] = useState(false);
  const [showExpected, setShowExpected] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await post(`/runs/${run.id}/feedback`, {
        rating,
        correction,
        ...(showExpected && checkType === "tests" && testCode.trim() ? { test_code: testCode } : {}),
        ...(showExpected && checkType === "file" && expectedContent
          ? { expected_name: expectedName, expected_content: expectedContent }
          : {}),
      });
      onSaved();
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="feedback-form" onSubmit={submit}>
      <div className="section-heading">
        <div className="heading-icon">
          <MessageSquare size={15} />
          <h3>Teach this agent</h3>
        </div>
        <div className="rating-buttons">
          <button
            type="button"
            className={cls(rating === "positive" && "selected-positive")}
            aria-label="Positive feedback"
            onClick={() => setRating("positive")}
          >
            <ThumbsUp size={14} />
          </button>
          <button
            type="button"
            className={cls(rating === "negative" && "selected-negative")}
            aria-label="Negative feedback"
            onClick={() => setRating("negative")}
          >
            <ThumbsDown size={14} />
          </button>
        </div>
      </div>
      {demo?.demo_correction && !run.feedback && (
        <button
          type="button"
          className="demo-correction-button"
          onClick={() => {
            setCorrection(demo.demo_correction);
            setExpectedName("clean.csv");
            setExpectedContent(demo.demo_expected || "");
            setShowExpected(true);
            setCheckType("file");
          }}
        >
          <Sparkles size={12} />
          Load demo teaching example
          <ArrowDown size={11} />
        </button>
      )}
      <textarea
        aria-label="Correction"
        placeholder="What should the agent do differently next time?"
        rows={3}
        value={correction}
        onChange={(e) => setCorrection(e.target.value)}
      />
      {!run.session_id && <button
        type="button"
        className="text-button expected-toggle"
        onClick={() => setShowExpected(!showExpected)}
      >
        {showExpected ? <ChevronDown size={13} /> : <Plus size={13} />}Add
        a regression check
      </button>}
      {run.session_id && <p className="tiny-copy">Chat feedback is saved for review. One-shot runs feed improvement batches; conversation-based evals are planned.</p>}
      {showExpected && !run.session_id && (
        <div className="expected-fields">
          <Field label="Regression check">
            <select value={checkType} onChange={(e) => setCheckType(e.target.value)}>
              <option value="tests">Python tests against delivered scripts</option>
              <option value="file">Expected CSV or JSON file</option>
            </select>
          </Field>
          {checkType === "tests" ? (
            <Field label="Python regression tests" hint="Define test_* functions with assertions. Delivered modules are importable by name; tests run in a fresh Docker sandbox and stay hidden from the agent.">
              <textarea className="code-text" rows={8} value={testCode} onChange={(e) => setTestCode(e.target.value)} placeholder={"def test_empty_input():\n    from script import transform\n    assert transform([]) == []"} />
            </Field>
          ) : <>
          <Field label="Expected output filename">
            <input
              value={expectedName}
              onChange={(e) => setExpectedName(e.target.value)}
              placeholder="output.csv"
            />
          </Field>
          <Field label="Expected file content">
            <textarea
              className="code-text"
              rows={5}
              value={expectedContent}
              onChange={(e) => setExpectedContent(e.target.value)}
              placeholder={"customer_id,name\n001,Ada"}
            />
          </Field>
          <label className="inline-link upload-expected">
            <Upload size={13} />
            Or upload expected file
            <input
              type="file"
              onChange={async (e) => {
                const file = e.target.files[0];
                if (file) {
                  setExpectedName(file.name);
                  setExpectedContent(await file.text());
                }
              }}
            />
          </label>
          </>}
        </div>
      )}
      <div className="feedback-footer">
        <span>
          {run.session_id ? "Saved for future conversation review" : showExpected && (checkType === "tests" ? testCode.trim() : expectedContent) ? (
            <>
              <Check size={12} />
              Eligible for evaluation
            </>
          ) : (
            "Checkable corrections power improvement"
          )}
        </span>
        <Button
          busy={busy}
          className="button-small"
          icon={Check}
          disabled={!correction.trim() && rating !== "positive"}
        >
          Save feedback
        </Button>
      </div>
    </form>
  );
}
function RunProvenance({ run, compact = false }) {
  const context = run.trace_context || {};
  return <div className={cls('run-provenance', compact && 'compact')}>
    <div className="run-purpose-line">
      <span className={cls('run-purpose', `purpose-${context.kind || 'one_shot'}`)}>{context.label || prettify(run.source)}</span>
      <span>{context.version_number ? `v${context.version_number}` : shortId(run.version_id)}</span>
      {context.case_split && <span className="run-split">{context.case_split === 'train' ? 'Training case' : context.case_split === 'validation' ? 'Validation case' : 'Holdout case'}</span>}
      {context.turn_index && <span>Turn {context.turn_index}</span>}
      {compact && run.source === 'eval' && run.outcome !== 'ungraded' && <span className={run.outcome === 'passed' ? 'text-teal' : 'text-red'}>{run.outcome === 'passed' ? 'Checks passed' : 'Checks failed'}</span>}
    </div>
    {context.case_name && <div className="run-case-name">{context.case_name}</div>}
    {context.phase && <div className="run-phase">{context.phase === 'confirmation' ? 'Validation confirmation' : context.phase === 'holdout' ? 'Reserved holdout audit' : 'Initial evaluation'}{context.attempt ? ` · attempt ${context.attempt}` : ''}</div>}
    {!compact && <>
      <div className="run-lineage">
        {context.model && <span>{context.model}</span>}
        {context.batch_id && <button className="text-button" onClick={() => window.dispatchEvent(new CustomEvent('open-batch', {detail: context.batch_id}))}>Open Batch {context.batch_number || ''} · {context.batch_id}</button>}
        {context.audit_id && <span>Audit {context.audit_id}</span>}
        {context.session_id && <span>Conversation {context.session_id}</span>}
      </div>
      {context.case_split === 'train' && <p className="tiny-copy">Training failures may inform the learner's next proposal.</p>}
      {context.case_split === 'validation' && <p className="tiny-copy">Used to compare candidates; case contents are withheld from the learner.</p>}
      {context.case_split === 'holdout' && <p className="tiny-copy">Reserved audit only; this result does not select or train candidates.</p>}
    </>}
  </div>;
}

function RunDetail({
  runId,
  onError,
  token,
  shared = false,
  onFeedback,
  onLoaded,
  demo,
}) {
  const [run, setRun] = useState(null);
  const [view, setView] = useState("trace");
  const [loadError, setLoadError] = useState("");
  const load = useCallback(async () => {
    try {
      const value = await api(
        `/runs/${runId}${token ? "?token=" + encodeURIComponent(token) : ""}`,
      );
      setRun(value);
      setLoadError("");
      onLoaded?.(value);
    } catch (e) {
      setLoadError(e.message);
    }
  }, [runId, token]);
  useEffect(() => {
    setRun(null);
    setView("trace");
    load();
  }, [runId, token]);
  usePolling(() => {
    if (!run || ACTIVE_RUN.includes(run.status)) load();
  }, 1500);
  if (loadError && !run) return <div className="inline-error">{loadError}</div>;
  if (!run) return <Spinner label="Loading run" />;
  const active = ACTIVE_RUN.includes(run.status);
  const events = run.events || [];
  const artifacts = run.artifacts || [];
  const outputs = artifacts.filter(
    (file) => file.role !== "input" && file.role !== "expected",
  );
  return (
    <div className="run-detail">
      <div className="run-detail-header">
        <div>
          <div className="eyebrow">
            RUN DETAILS <span>#{shortId(run.id)}</span>
          </div>
          <h3>{run.trace_context?.case_name || (run.session_id ? `Chat turn ${run.turn_index}` : (run.prompt || 'Agent execution').slice(0, 120))}</h3>
        </div>
        <Badge status={run.status} />
      </div>
      <RunProvenance run={run}/>
      <details className="run-task"><summary>Task prompt</summary><div>{run.prompt}</div></details>
      <div className="run-detail-metrics">
        <div>
          <span>Duration</span>
          <strong>{duration(run.latency_ms)}</strong>
        </div>
        <div>
          <span>Model cost</span>
          <strong>
            {money(run.cost_usd)}
            {(run.cost_source || run.cost_kind) === "estimated" && (
              <small> est.</small>
            )}
          </strong>
        </div>
        <div>
          <span>Tool calls</span>
          <strong>{run.tool_calls ?? "—"}</strong>
        </div>
        <div>
          <span>Outcome</span>
          <strong
            className={cls(
              "outcome",
              run.outcome === "passed" && "text-teal",
              run.outcome === "failed" && "text-red",
            )}
          >
            {prettify(run.outcome || "ungraded")}
          </strong>
        </div>
      </div>
      <div className="run-context">
        <span>
          <GitBranch size={12} />
          {run.version_number
            ? `v${run.version_number}`
            : shortId(run.version_id)}
        </span>
        <span>{prettify(run.source || "playground")}</span>
        <span>{date(run.created_at)}</span>
      </div>
      {loadError && <div className="inline-error">{loadError}</div>}
      {run.error && (
        <div className="inline-error">
          <XCircle size={15} />
          <div>
            <strong>Execution failed</strong>
            <p>{typeof run.error === "string" ? run.error : json(run.error)}</p>
          </div>
        </div>
      )}
      <div className="detail-tabs">
        <button
          className={view === "trace" ? "active" : ""}
          onClick={() => setView("trace")}
        >
          <Waypoints size={14} />
          Trace<span>{events.length}</span>
        </button>
        <button
          className={view === "output" ? "active" : ""}
          onClick={() => setView("output")}
        >
          <FileOutput size={14} />
          Output{outputs.length > 0 && <span>{outputs.length}</span>}
        </button>
      </div>
      {view === "trace" ? (
        <div className="trace-timeline">
          {events.length ? (
            events.map((event, index) => (
              <TraceEvent
                key={event.seq ?? index}
                event={event}
                index={index}
              />
            ))
          ) : (
            <div className="trace-waiting">
              {active ? (
                <>
                  <Loader2 className="spin" size={16} />
                  Waiting for execution events…
                </>
              ) : (
                <span className="muted">
                  No trace events were recorded for this run.
                </span>
              )}
            </div>
          )}
          {active && events.length > 0 && (
            <div className="trace-live">
              <span className="pulse-dot" />
              Execution in progress<span className="muted">Live updates</span>
            </div>
          )}
        </div>
      ) : (
        <div className="output-view">
          {run.output ? (
            <div className="output-text">
              {typeof run.output === "string" ? run.output : json(run.output)}
            </div>
          ) : (
            <p className="muted">
              {active
                ? "The final response will appear here when the run finishes."
                : "No final response was returned."}
            </p>
          )}
          {outputs.length > 0 && (
            <div className="artifact-files">
              {outputs.map((file) => <ArtifactFile key={file.id} file={file} token={token}/>)}
            </div>
          )}
        </div>
      )}
      {!active && outputs.length > 0 && view === "trace" && (
        <div className="trace-artifacts">
          <span className="muted">OUTPUT FILES</span>
          {outputs.map((file) => <ArtifactFile key={file.id} file={file} token={token}/>)}
        </div>
      )}
      {!shared && run.status === "completed" && (
        <FeedbackForm
          key={run.id}
          run={run}
          demo={demo}
          onError={onError}
          onSaved={async () => {
            await load();
            onFeedback?.();
          }}
        />
      )}
    </div>
  );
}
function RunsView({ agent, runs, selectedRun, onSelect, onError, onRefresh }) {
  const [purpose, setPurpose] = useState("all");
  const [status, setStatus] = useState("all");
  const [batch, setBatch] = useState("all");
  const [split, setSplit] = useState("all");
  const [search, setSearch] = useState("");
  const contexts = runs.map(r => r.trace_context || {});
  const batches = [...new Map(contexts.filter(c => c.batch_id).map(c => [c.batch_id, c])).values()];
  function matches(run) {
    const c = run.trace_context || {};
    const haystack = [run.prompt, run.id, c.label, c.case_name, c.model, c.batch_id, c.session_id, c.version_number ? `v${c.version_number}` : ''].join(' ').toLowerCase();
    return (purpose === 'all' || c.kind === purpose) && (status === 'all' || run.status === status) &&
      (batch === 'all' || c.batch_id === batch) && (split === 'all' || c.case_split === split) && haystack.includes(search.toLowerCase());
  }
  // A trace opened from a comparison must be visible even after prior filtering.
  useEffect(() => {
    const selected = runs.find(r => r.id === selectedRun);
    if (selected && !matches(selected)) { setPurpose('all'); setStatus('all'); setBatch('all'); setSplit('all'); setSearch(''); }
  }, [selectedRun]);
  const filtered = runs.filter(matches);
  const groups = new Map();
  for (const run of filtered) {
    const c = run.trace_context || {};
    const key = c.audit_id || c.batch_id || c.session_id || 'one-shot';
    const title = c.audit_id ? `Holdout audit · Batch ${c.batch_number || '?'}` : c.batch_id ? `Batch ${c.batch_number || '?'} · ${c.batch_id.slice(-6)}` : c.session_id ? `${c.kind === 'team_chat' ? 'Deployed' : 'Builder'} conversation · ${c.session_id.slice(-6)}` : 'One-shot requests';
    if (!groups.has(key)) groups.set(key, {title, runs: []});
    groups.get(key).runs.push(run);
  }
  return <div className={cls('runs-layout', selectedRun && 'has-selection')}>
    <section className="card run-history">
      <div className="card-header"><div className="heading-icon"><Activity size={16}/><h3>Run history</h3><span className="count">{filtered.length}/{runs.length}</span></div><button className="icon-button" aria-label="Refresh runs" onClick={onRefresh}><RefreshCw size={14}/></button></div>
      <div className="run-filters organized-run-filters">
        <input aria-label="Search traces" placeholder="Search task, case, model or run ID…" value={search} onChange={e => setSearch(e.target.value)}/>
        <select aria-label="Filter run purpose" value={purpose} onChange={e => setPurpose(e.target.value)}>
          <option value="all">All activity</option><option value="chat">Builder chats</option><option value="team_chat">Deployed chats</option><option value="one_shot">One-shot requests</option><option value="baseline">Baseline evaluations</option><option value="candidate">Candidate evaluations</option><option value="holdout">Holdout audits</option><option value="evaluation">Other evaluations</option>
        </select>
        <select aria-label="Filter evaluation batch" value={batch} onChange={e => setBatch(e.target.value)}><option value="all">All batches</option>{batches.map(c => <option key={c.batch_id} value={c.batch_id}>Batch {c.batch_number} · {c.batch_id.slice(-6)}</option>)}</select>
        <select aria-label="Filter evaluation split" value={split} onChange={e => setSplit(e.target.value)}><option value="all">All case splits</option><option value="train">Training cases</option><option value="validation">Validation cases</option><option value="holdout">Holdout cases</option></select>
        <select aria-label="Filter run status" value={status} onChange={e => setStatus(e.target.value)}><option value="all">All statuses</option>{['completed','running','queued','failed','interrupted'].map(s => <option key={s} value={s}>{prettify(s)}</option>)}</select>
      </div>
      {!filtered.length ? <Empty icon={Waypoints} title="No matching traces">Change the filters or search to find a conversation or evaluation run.</Empty> :
        <div className="run-list">{[...groups.entries()].map(([key, group]) => <div key={key} className="run-group">
          <div className="run-group-heading">{group.title}<span>{group.runs.length}</span></div>
          {group.runs.map(run => <button key={run.id} className={cls('run-row', selectedRun === run.id && 'selected')} onClick={() => onSelect(run.id)}>
            <div className="run-row-top"><Badge status={run.status}/><span>{date(run.created_at)}</span></div>
            <RunProvenance run={run} compact/>
            <strong>{run.prompt || 'Agent execution'}</strong>
            <div className="run-row-bottom"><span>{run.id.slice(-6)}</span><span>{duration(run.latency_ms)}</span><span>{money(run.cost_usd)}</span>{run.feedback && <MessageSquare size={12}/>}</div><ChevronRight className="run-row-arrow" size={14}/>
          </button>)}
        </div>)}</div>}
    </section>
    <section className="card trace-card">{selectedRun ? <RunDetail runId={selectedRun} onError={onError} onFeedback={onRefresh} demo={agent.demo ? agent : null}/> : <Empty icon={Waypoints} title="Follow the execution">Choose a labeled conversation, baseline or candidate run to inspect its trace and files.</Empty>}</section>
  </div>;
}

function NewCaseModal({ agentId, onClose, onSaved, onError }) {
  const [form, setForm] = useState({
    name: "",
    prompt: "",
    files: [],
    expected_name: "output.csv",
    expected_content: "",
    split: "train",
  });
  const [busy, setBusy] = useState(false);
  const set = (key, value) => setForm({ ...form, [key]: value });
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await post(`/agents/${agentId}/cases`, form);
      onSaved();
      onClose();
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Add an evaluation case"
      subtitle="A task and an expected file give the improvement loop something concrete to measure."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <Field label="Case name">
          <input
            required
            autoFocus
            placeholder="e.g. Preserve the newest customer record"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
          />
        </Field>
        <Field label="Task">
          <textarea
            rows={3}
            required
            value={form.prompt}
            onChange={(e) => set("prompt", e.target.value)}
            placeholder="Describe the transformation and output filename"
          />
        </Field>
        <div className="field">
          <span className="field-label">Input files</span>
          <FilePicker
            files={form.files}
            onChange={(value) => set("files", value)}
          />
        </div>
        <div className="form-columns">
          <Field label="Expected output filename">
            <input
              value={form.expected_name}
              required
              onChange={(e) => set("expected_name", e.target.value)}
            />
          </Field>
          <Field label="Case visibility">
            <select
              value={form.split}
              onChange={(e) => set("split", e.target.value)}
            >
              <option value="train">Training · visible to improver</option>
              <option value="validation">
                Validation · hidden from improver
              </option>
              <option value="holdout">Holdout · final assessment</option>
            </select>
          </Field>
        </div>
        <Field label="Expected file content">
          <textarea
            className="code-text"
            rows={5}
            required
            value={form.expected_content}
            onChange={(e) => set("expected_content", e.target.value)}
            placeholder={"customer_id,name\n001,Ada"}
          />
        </Field>
        <label className="inline-link upload-expected">
          <Upload size={13} />
          Or upload expected file
          <input
            type="file"
            onChange={async (e) => {
              const file = e.target.files[0];
              if (file)
                setForm({
                  ...form,
                  expected_name: file.name,
                  expected_content: await file.text(),
                });
            }}
          />
        </label>
        <div className="modal-footer">
          <Button type="button" className="button-quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button className="button-primary" busy={busy} icon={Plus}>
            Add case
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function CaseOutcome({ passed, error }) {
  return passed === true ? (
    <span className="case-result result-pass">
      <Check size={13} />
      Pass
    </span>
  ) : passed === false ? (
    <span
      className="case-result result-fail"
      title={error || "Output did not match the expected artifact"}
    >
      <X size={13} />
      Fail
    </span>
  ) : error ? (
    <span className="case-result result-error" title={error}>
      <XCircle size={13} />
      Error
    </span>
  ) : (
    <span className="muted">—</span>
  );
}
function FinalAudit({ agentId, batchId, onError }) {
  const [audit, setAudit] = useState(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    api(`/agents/${agentId}/audits`)
      .then((values) => {
        setAudit(values[0] || null);
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, [agentId]);
  usePolling(async () => {
    if (audit && ["queued", "running"].includes(audit.status)) {
      try {
        setAudit(await api(`/audits/${audit.id}`));
      } catch (e) {
        onError(e.message);
      }
    }
  }, 1800);
  async function start() {
    setBusy(true);
    try {
      setAudit(await post(`/agents/${agentId}/audits`, { batch_id: batchId }));
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const summary = audit?.summary || {};
  const active = audit && ["queued", "running"].includes(audit.status);
  return (
    <section className="final-audit">
      <div className="section-heading">
        <div className="heading-icon">
          <ShieldCheck size={16} />
          <h3>Final holdout report</h3>
        </div>
        {audit && <Badge status={audit.status} />}
      </div>
      {audit ? (
        <>
          <p className="audit-note">
            {audit.batch_id !== batchId
              ? "This agent’s final report was collected for another candidate. "
              : ""}
            Reserved cases are assessed once, separately from the improvement
            gate.
          </p>
          {active && (
            <div className="audit-working">
              <Loader2 className="spin" size={14} />
              Running fresh final trials…
              <span>
                {summary.completed || 0} / {summary.total || "—"} pairs complete
              </span>
            </div>
          )}
          {audit.error && <div className="inline-error">{audit.error}</div>}
          {audit.results?.length > 0 && (
            <>
              <div className="audit-scores">
                <span>
                  Parent{" "}
                  <strong>
                    {summary.parent_passed ?? "—"} / {summary.total ?? "—"}
                  </strong>
                </span>
                <ArrowRight size={14} />
                <span>
                  Candidate{" "}
                  <strong>
                    {summary.candidate_passed ?? "—"} / {summary.total ?? "—"}
                  </strong>
                </span>
                {summary.incomplete > 0 && (
                  <span className="audit-incomplete">
                    {summary.incomplete} incomplete
                  </span>
                )}
              </div>
              <div className="audit-result-list">
                {audit.results.map((result, index) => (
                  <div key={result.case_id || index}>
                    <span>{result.name || "Final case"}</span>
                    <CaseOutcome
                      passed={result.parent_passed}
                      error={result.parent_error}
                    />
                    <ArrowRight size={11} />
                    <CaseOutcome
                      passed={result.candidate_passed}
                      error={result.candidate_error}
                    />
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      ) : (
        <>
          <p className="audit-note">
            Evaluate the parent and candidate on untouched final cases. One
            report per agent, preserved even if interrupted. These results never
            feed the improver.
          </p>
          <Button
            className="button-small"
            icon={Beaker}
            busy={busy}
            disabled={!loaded}
            onClick={start}
          >
            Run final holdout comparison
          </Button>
        </>
      )}
    </section>
  );
}
function BatchDetail({ batchId, agentId, onError, onRefresh }) {
  const [batch, setBatch] = useState(null);
  const [busy, setBusy] = useState("");
  const [loadError, setLoadError] = useState("");
  const load = useCallback(async () => {
    try {
      setBatch(await api(`/improvements/${batchId}`));
      setLoadError("");
    } catch (e) {
      setLoadError(e.message);
    }
  }, [batchId]);
  useEffect(() => {
    setBatch(null);
    load();
  }, [batchId]);
  usePolling(() => {
    if (!batch || ACTIVE_BATCH.includes(batch.status)) load();
  }, 1500);
  async function decide(action) {
    setBusy(action);
    try {
      await post(`/improvements/${batchId}/${action}`);
      await load();
      await onRefresh();
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy("");
    }
  }
  if (!batch)
    return loadError ? (
      <div className="inline-error">{loadError}</div>
    ) : (
      <Spinner label="Loading improvement batch" />
    );
  const active = ACTIVE_BATCH.includes(batch.status);
  const summary = batch.summary || {};
  const results = batch.results || [];
  const eligible = batch.status === "awaiting_review";
  const oldLines = (batch.old_playbook || "").split("\n").filter(Boolean);
  const newLines = (batch.new_playbook || "").split("\n").filter(Boolean);
  return (
    <div className="batch-detail">
      <div className="card-header">
        <div>
          <div className="eyebrow">
            IMPROVEMENT BATCH <span>#{shortId(batch.id)}</span>
          </div>
          <h3>
            {active
              ? "Testing a better approach"
              : batch.status === "promoted"
                ? "A new lesson, deployed"
                : batch.status === "awaiting_review"
                  ? "A candidate is ready for review"
                  : batch.status === "no_gain"
                    ? "No measurable gain this time"
                    : "Candidate comparison"}
          </h3>
        </div>
        <Badge status={batch.status} />
      </div>
      {loadError && <div className="inline-error">{loadError}</div>}
      {active && (
        <p className="tiny-copy">{batch.progress ? `${batch.progress.completed} runs finished · ${batch.progress.running} running · ${batch.progress.queued} queued` : `${batch.run_count || 0} runs scheduled`} · at most {batch.progress?.planned_max ?? batch.planned_max_runs ?? batch.max_runs} runs including confirmation</p>
      )}
      {active && (
        <div className="batch-progress">
          {["analyzing", "proposing", "evaluating", "confirming"].map((stage, index) => {
            const current = ["queued", "analyzing"].includes(batch.status)
              ? 0
              : batch.status === "proposing"
                ? 1
                : batch.status === "confirming" ? 3 : 2;
            return (
              <div
                key={stage}
                className={cls(
                  "batch-stage",
                  current >= index && "stage-active",
                )}
              >
                <span>
                  {current > index ? (
                    <Check size={14} />
                  ) : current === index ? (
                    <Loader2 className="spin" size={14} />
                  ) : (
                    index + 1
                  )}
                </span>
                <strong>
                  {stage === "analyzing"
                    ? "Review evidence"
                    : stage === "proposing"
                      ? "Propose playbook"
                      : stage === "confirming" ? "Confirm finalist" : "Run evaluations"}
                </strong>
              </div>
            );
          })}
        </div>
      )}
      {batch.error && (
        <div className="inline-error">
          {typeof batch.error === "string" ? batch.error : json(batch.error)}
        </div>
      )}
      {batch.candidates?.length > 0 && (
        <div className="candidate-table">
          <div className="diff-heading">
            Candidates tested
            <span>
              Score = mean fraction of hidden tests passed · reward = score − {batch.reward_config?.cost_weight ?? 1} × $ per task
            </span>
          </div>
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Model · turns</th>
                <th>Score</th>
                <th>Cost / task</th>
                <th>Reward</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              <tr className="muted">
                <td>Current (parent)</td>
                <td>
                  {batch.parent_model || "—"} · {batch.parent_max_turns ?? "—"}
                </td>
                <td>{pct(batch.parent_metrics?.pass_rate)}</td>
                <td>{money(batch.parent_metrics?.cost_per_task_usd)}</td>
                <td>{num(batch.parent_metrics?.reward)}</td>
                <td>baseline</td>
              </tr>
              {batch.candidates.map((c, i) => (
                <tr
                  key={c.version_id}
                  className={c.version_id === batch.candidate_version_id ? "chosen" : ""}
                  title={c.rationale}
                >
                  <td>
                    Candidate {i + 1}
                    {c.version_id === batch.candidate_version_id && " ★"}
                  </td>
                  <td>
                    {c.model || "—"} · {c.max_turns ?? "—"}
                  </td>
                  <td>{pct(c.metrics?.pass_rate)}</td>
                  <td>{money(c.metrics?.cost_per_task_usd)}</td>
                  <td>{num(c.metrics?.reward)}</td>
                  <td className={c.qualifies ? "text-teal" : "muted"}>
                    {c.qualifies ? "qualifies" : c.reason}
                    {c.confirmed && " · confirmed"}
                    {` · +${c.summary?.fixed ?? 0} / −${c.summary?.regressed ?? 0}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {batch.promoted_by === "automatic" && (
            <p className="tiny-copy">Promoted automatically: passed the gate with auto-promote enabled.</p>
          )}
        </div>
      )}
      {batch.rationale && (
        <div className="rationale">
          <Sparkles size={17} />
          <div>
            <strong>What changed, and why</strong>
            <p>{batch.rationale}</p>
          </div>
        </div>
      )}
      {(batch.new_playbook || batch.old_playbook) && (
        <div className="playbook-diff">
          <div className="diff-heading">
            <GitBranch size={14} />
            Playbook revision<span>Base instructions stay fixed; model and turn limit may change</span>
          </div>
          {oldLines.length === 0 && (
            <div className="diff-line neutral">
              <span> </span>No learned rules in parent version
            </div>
          )}
          {oldLines
            .filter((line) => !newLines.includes(line))
            .map((line, i) => (
              <div key={"old" + i} className="diff-line removed">
                <span>−</span>
                {line}
              </div>
            ))}
          {newLines.map((line, i) => (
            <div
              key={"new" + i}
              className={cls(
                "diff-line",
                oldLines.includes(line) ? "neutral" : "added",
              )}
            >
              <span>{oldLines.includes(line) ? " " : "+"}</span>
              {line}
            </div>
          ))}
        </div>
      )}
      {(summary.total != null || results.length > 0) && (
        <>
          <div className="comparison-summary">
            <div>
              <span>Parent</span>
              <strong>
                {summary.parent_passed ??
                  results.filter((r) => r.parent_passed).length}
                <small> / {summary.total ?? results.length}</small>
              </strong>
            </div>
            <ArrowRight size={18} />
            <div>
              <span>Candidate</span>
              <strong className="text-teal">
                {summary.candidate_passed ??
                  results.filter((r) => r.candidate_passed).length}
                <small> / {summary.total ?? results.length}</small>
              </strong>
            </div>
            <div className="comparison-delta">
              <span className="delta-fixed">
                +
                {summary.fixed ??
                  results.filter((r) => !r.parent_passed && r.candidate_passed)
                    .length}{" "}
                fixed
              </span>
              <span className={summary.regressed || 0 ? "text-red" : "muted"}>
                {summary.regressed ??
                  results.filter((r) => r.parent_passed && !r.candidate_passed)
                    .length}{" "}
                regressions
              </span>
            </div>
          </div>
          <div className="case-table-wrap">
            <table className="case-table">
              <thead>
                <tr>
                  <th>Evaluation case</th>
                  <th>Parent</th>
                  <th>Candidate</th>
                </tr>
              </thead>
              <tbody>
                {results.map((result, index) => (
                  <tr key={result.case_id || index}>
                    <td>
                      <span className="case-name">
                        {result.name || shortId(result.case_id)}
                      </span>
                      {(result.parent_run_id || result.candidate_run_id) && (
                        <span className="case-trace-links">
                          {result.parent_run_id && (
                            <a
                              href={`?view=runs&run=${encodeURIComponent(result.parent_run_id)}`}
                              onClick={(e) => {
                                e.preventDefault();
                                window.dispatchEvent(
                                  new CustomEvent("open-run", {
                                    detail: result.parent_run_id,
                                  }),
                                );
                              }}
                            >
                              Parent trace
                            </a>
                          )}
                          {result.candidate_run_id && (
                            <a
                              href={`?view=runs&run=${encodeURIComponent(result.candidate_run_id)}`}
                              onClick={(e) => {
                                e.preventDefault();
                                window.dispatchEvent(
                                  new CustomEvent("open-run", {
                                    detail: result.candidate_run_id,
                                  }),
                                );
                              }}
                            >
                              Candidate trace
                            </a>
                          )}
                        </span>
                      )}
                    </td>
                    <td>
                      <CaseOutcome
                        passed={result.parent_passed}
                        error={result.parent_error}
                      />
                      {result.parent_rate != null && <small> {pct(result.parent_rate)}</small>}
                    </td>
                    <td>
                      <CaseOutcome
                        passed={result.candidate_passed}
                        error={result.candidate_error}
                      />
                      {result.candidate_rate != null && <small> {pct(result.candidate_rate)} · {result.trials} trial{result.trials === 1 ? "" : "s"}</small>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="evaluation-note">
            <ShieldCheck size={13} />
            Paired runs in fresh workspaces. Small-sample results are
            preliminary.
          </p>
        </>
      )}
      {eligible && (
        <div className="promotion-footer">
          <div>
            <CheckCheck size={18} />
            <span>
              <strong>Promotion gate passed</strong>{summary.reason || "Measured reward improved with no material case regression."}
            </span>
          </div>
          <div>
            <Button
              className="button-quiet"
              busy={busy === "reject"}
              disabled={!!busy}
              onClick={() => decide("reject")}
            >
              Reject
            </Button>
            <Button
              className="button-primary"
              icon={Rocket}
              busy={busy === "promote"}
              disabled={!!busy}
              onClick={() => decide("promote")}
            >
              Promote version
            </Button>
          </div>
        </div>
      )}
      {batch.status === "promoted" && (
        <div className="promotion-success">
          <CheckCheck size={17} />
          Promoted. Future runs use this tested playbook.
        </div>
      )}
      {batch.status === "no_gain" && (
        <div className="no-gain">
          <ShieldCheck size={18} />
          <p>
            The candidate did not clear the quality gate. The current version
            remains active; your evidence is preserved.
          </p>
        </div>
      )}
      {["awaiting_review", "promoted", "no_gain"].includes(batch.status) &&
        batch.candidate_version_id && (
          <FinalAudit agentId={agentId} batchId={batch.id} onError={onError} />
        )}
    </div>
  );
}
const KIND_LABEL = { tests: "Code tests", file: "Expected file", rubric: "Judge rubric" };
function caseKind(item) {
  return item.kind || (item.test_code ? "tests" : item.expected_name ? "file" : "rubric");
}
function EvalsView({ agent, cases, onRefresh, onError }) {
  const [generation, setGeneration] = useState(null);
  const [busy, setBusy] = useState("");
  const [count, setCount] = useState(8);
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState(null);
  const loadGeneration = useCallback(async () => {
    try {
      const items = await api(`/agents/${agent.id}/generations`);
      setGeneration(items[0] || null);
      return items[0];
    } catch (e) {
      return null;
    }
  }, [agent.id]);
  useEffect(() => {
    loadGeneration();
  }, [agent.id]);
  const generating = generation?.status === "running";
  usePolling(async () => {
    if (!generating) return;
    await loadGeneration();
    await onRefresh();
  }, 3000);
  async function generate() {
    setBusy("generate");
    try {
      setGeneration(await post(`/agents/${agent.id}/cases/generate`, { count: Number(count) || 8 }));
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy("");
    }
  }
  const splits = {};
  for (const c of cases) splits[c.split] = (splits[c.split] || 0) + 1;
  const kinds = {};
  for (const c of cases) kinds[caseKind(c)] = (kinds[caseKind(c)] || 0) + 1;
  const shown = cases.filter(
    (c) => filter === "all" || c.split === filter || caseKind(c) === filter,
  );
  return (
    <div className="evals-view">
      <section className="card">
        <div className="card-header">
          <div className="heading-icon">
            <Beaker size={16} />
            <h3>Eval suite</h3>
            <span className="count">{cases.length}</span>
          </div>
          <div className="evals-actions">
            <label className="turns-field">
              Cases
              <input type="number" min="1" max="80" value={count} onChange={(e) => setCount(e.target.value)} />
            </label>
            <Button className="button-primary" icon={Sparkles} busy={busy === "generate" || generating} disabled={generating} onClick={generate}>
              Generate evals
            </Button>
          </div>
        </div>
        <div className="evals-summary">
          {["train", "validation", "holdout"].map((d) =>
            splits[d] ? (
              <button key={d} className={cls("difficulty-chip", filter === d && "selected")} onClick={() => setFilter(filter === d ? "all" : d)}>
                {d} <strong>{splits[d]}</strong>
              </button>
            ) : null,
          )}
          {Object.entries(kinds).map(([k, n]) => (
            <button key={k} className={cls("difficulty-chip", filter === k && "selected")} onClick={() => setFilter(filter === k ? "all" : k)}>
              {KIND_LABEL[k]} <strong>{n}</strong>
            </button>
          ))}
        </div>
        {generation && (
          <p className="generation-status">
            {generating
              ? `Generating: ${generation.stage}. ${generation.drafted || 0} drafted, ${generation.created_case_ids?.length || 0} validated so far…`
              : generation.status === "completed"
                ? `Last generation kept ${generation.created_case_ids?.length || 0} of ${generation.drafted || 0} drafted cases; ${generation.requested} requested (${generation.rejected?.length || 0} rejected by validation) · ${money(generation.cost_usd)}`
                : `Last generation ${generation.status}: ${generation.error || ""}`}
            {generation.batch_id && " · Hill climbing started automatically: see the Improve tab."}
            {generation.climb_error && (
              <span className="text-red"> · Climb did not start: {generation.climb_error}</span>
            )}
          </p>
        )}
        {cases.length ? (
          <table className="evals-table">
            <thead>
              <tr>
                <th>Case</th>
                <th>Type</th>
                <th>Split</th>
                <th>Category</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((c) => (
                <React.Fragment key={c.id}>
                  <tr className="evals-row" onClick={() => setOpen(open === c.id ? null : c.id)}>
                    <td>
                      <strong>{c.name}</strong>
                      <small>{c.origin === "generated" ? "generated" : c.source_run_id ? "from feedback" : "hand-written"}</small>
                    </td>
                    <td>
                      {KIND_LABEL[caseKind(c)]}
                      {c.test_count ? <small>{c.test_count} tests</small> : null}
                    </td>
                    <td>{c.split}</td>
                    <td>{c.category || "—"}</td>
                  </tr>
                  {open === c.id && (
                    <tr className="evals-detail">
                      <td colSpan={4}>
                        <p className="evals-prompt">{c.prompt}</p>
                        {c.source_run_id && <a className="inline-link" href={`?agent=${agent.id}&view=runs&run=${c.source_run_id}`}>View source request and trace</a>}
                        {c.files?.map((f) => <details key={f.name}><summary>Input: {f.name}</summary><pre className="evals-code">{f.content}</pre></details>)}
                        {c.test_code && (
                          <>
                            <div className="diff-heading">Hidden tests<span>Never shown to the agent</span></div>
                            <pre className="evals-code">{c.test_code}</pre>
                          </>
                        )}
                        {c.expected_name && <p className="tiny-copy">Expected output file: {c.expected_name}</p>}
                        {c.expected_content != null && <pre className="evals-code">{c.expected_content}</pre>}
                        {c.reference_files?.map((f) => <details key={f.name}><summary>Reference solution: {f.name}</summary><pre className="evals-code">{f.content}</pre></details>)}
                        {c.rubric?.length > 0 && (
                          <ul className="rubric-list">
                            {c.rubric.map((r, i) => <li key={i}>{r}</li>)}
                          </ul>
                        )}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="small-empty">
            <Beaker size={21} />
            <p>No evals yet</p>
            <span>Generate a suite: a strong model drafts cases with hidden tests, and only cases whose reference solution passes are kept.</span>
          </div>
        )}
      </section>
    </div>
  );
}
function ImproveView({ agent, cases, batches, onRefresh, onError, initialBatchId }) {
  const batchNumbers = new Map([...batches].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)).map((b, i) => [b.id, i + 1]));
  const [selected, setSelected] = useState(initialBatchId || batches[0]?.id || null);
  const [busy, setBusy] = useState(false);
  const [showCase, setShowCase] = useState(false);
  const [toggleBusy, setToggleBusy] = useState(false);
  const [generation, setGeneration] = useState(null);
  const [genBusy, setGenBusy] = useState(false);
  const [costWeight, setCostWeight] = useState(agent.cost_weight ?? 1);
  useEffect(() => {
    if (!selected && batches[0]) setSelected(batches[0].id);
  }, [batches]);
  useEffect(() => { if (initialBatchId) setSelected(initialBatchId); }, [initialBatchId]);
  useEffect(() => {
    api(`/agents/${agent.id}/generations`)
      .then((items) => setGeneration(items[0] || null))
      .catch(() => {});
  }, [agent.id]);
  usePolling(async () => {
    if (generation?.status !== "running") return;
    try {
      const items = await api(`/agents/${agent.id}/generations`);
      setGeneration(items[0] || null);
      if (items[0]?.status !== "running") await onRefresh();
    } catch (e) {
      onError(e.message);
    }
  }, 2000);
  async function generate() {
    setGenBusy(true);
    try {
      setGeneration(await post(`/agents/${agent.id}/cases/generate`, { count: 8 }));
    } catch (e) {
      onError(e.message);
    } finally {
      setGenBusy(false);
    }
  }
  async function patch(body) {
    setToggleBusy(true);
    try {
      await api(`/agents/${agent.id}`, { method: "PATCH", body });
      await onRefresh();
    } catch (e) {
      onError(e.message);
    } finally {
      setToggleBusy(false);
    }
  }
  const active = batches.find(
    (batch) =>
      ACTIVE_BATCH.includes(batch.status) || batch.status === "awaiting_review",
  );
  async function improve() {
    setBusy(true);
    try {
      const batch = await post(`/agents/${agent.id}/improvements`);
      setSelected(batch.id);
      await onRefresh();
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function toggle() {
    setToggleBusy(true);
    try {
      await api(`/agents/${agent.id}`, {
        method: "PATCH",
        body: { auto_improve: !agent.auto_improve },
      });
      await onRefresh();
    } catch (e) {
      onError(e.message);
    } finally {
      setToggleBusy(false);
    }
  }
  return (
    <>
      <div className="improve-intro">
        <div className="improve-symbol">
          <Sparkles size={25} />
        </div>
        <div>
          <h3>A better agent, backed by evidence.</h3>
          <p>
            Turn corrections and traces into a tested playbook. You decide what
            gets published.
          </p>
        </div>
        <Button
          className="button-primary"
          icon={Sparkles}
          busy={busy}
          disabled={!!active || !cases.some((item) => item.split !== "holdout")}
          onClick={improve}
        >
          {active ? "Batch in progress" : "Improve now"}
        </Button>
      </div>
      <div className="improve-layout">
        <aside className="improve-aside">
          <section className="card">
            <div className="card-header">
              <div className="heading-icon">
                <Radio size={16} />
                <h3>Continuous learning</h3>
              </div>
              <button
                className={cls("toggle", agent.auto_improve && "toggle-on")}
                onClick={toggle}
                disabled={toggleBusy}
                role="switch"
                aria-checked={!!agent.auto_improve}
                aria-label="Automatic improvement"
              >
                <span />
              </button>
            </div>
            <div className="card-body">
              <p className="small-copy">
                Collect evidence continuously. Start a bounded improvement batch
                after <strong>3 new labeled cases</strong>.
              </p>
              <div className="evidence-meter">
                <strong>
                  {agent.eligible_evidence ?? "—"}
                  <span> / 3</span>
                </strong>
                <div>
                  <span>new labeled cases</span>
                  <small>Evidence for the next batch</small>
                </div>
              </div>
              {agent.needs_label > 0 && (
                <p className="needs-label">
                  <MessageSquare size={12} />
                  {agent.needs_label} corrections need expected output
                </p>
              )}
              <div className="automation-state">
                <span
                  className={cls(
                    "status-dot",
                    agent.auto_improve && "teal-dot",
                  )}
                />
                {agent.auto_improve
                  ? "Automatic batching enabled"
                  : "Manual batches only"}
              </div>
              <div className="automation-row">
                <span>Auto-promote winners</span>
                <button
                  className={cls("toggle", agent.auto_promote && "toggle-on")}
                  onClick={() => patch({ auto_promote: !agent.auto_promote })}
                  disabled={toggleBusy}
                  role="switch"
                  aria-checked={!!agent.auto_promote}
                  aria-label="Automatic promotion"
                >
                  <span />
                </button>
              </div>
              <label className="automation-row">
                <span>
                  Cost weight
                  <small>1 = one cent per task costs one point of pass rate</small>
                </span>
                <input
                  type="number"
                  min="0"
                  step="0.5"
                  value={costWeight}
                  onChange={(e) => setCostWeight(e.target.value)}
                  onBlur={() => patch({ cost_weight: Number(costWeight) || 0 })}
                />
              </label>
              <p className="tiny-copy">
                {agent.auto_promote
                  ? "Candidates that pass the gate deploy automatically."
                  : "Candidates are reviewed before promotion."}
              </p>
            </div>
          </section>
          <section className="card evidence-card">
            <div className="card-header">
              <div className="heading-icon">
                <Beaker size={16} />
                <h3>Evaluation cases</h3>
                <span className="count">{cases.length}</span>
              </div>
              <Button
                className="button-small"
                icon={Sparkles}
                busy={genBusy || generation?.status === "running"}
                disabled={generation?.status === "running"}
                onClick={generate}
              >
                Generate
              </Button>
              <button
                className="icon-button"
                aria-label="Add evaluation case"
                onClick={() => setShowCase(true)}
              >
                <Plus size={16} />
              </button>
            </div>
            {generation && (
              <p className="generation-status">
                {generation.status === "running"
                  ? "Drafting cases and running reference solutions…"
                  : generation.status === "error"
                    ? `Generation failed: ${generation.error}`
                    : `Generated ${generation.created_case_ids?.length ?? 0} validated cases` +
                      (generation.rejected?.length ? `, rejected ${generation.rejected.length}` : "")}
              </p>
            )}
            {cases.length ? (
              <div className="case-list">
                {cases.map((item) => (
                  <details className="evidence-case" key={item.id}>
                    <summary>
                      <span className="case-icon">
                        <FileText size={14} />
                      </span>
                      <span>
                        {item.name || "Correction from run"}
                        <small>
                          {item.split || "train"}
                          {item.source_run_id && " · from feedback"}
                          {item.origin === "generated" && " · generated"}
                          {item.rubric?.length > 0 && " · judged"}
                        </small>
                      </span>
                      <ChevronDown size={12} />
                    </summary>
                    <div className="case-expanded">
                      <p>{item.prompt}</p>
                      {item.source_run_id && (
                        <button
                          className="text-button"
                          onClick={() =>
                            window.dispatchEvent(
                              new CustomEvent("open-run", {
                                detail: item.source_run_id,
                              }),
                            )
                          }
                        >
                          View source trace
                          <ArrowUpRight size={12} />
                        </button>
                      )}
                      {item.expected_name && <code>{item.expected_name}</code>}
                      {item.rubric?.length > 0 && (
                        <ul className="rubric-list">
                          {item.rubric.map((r, i) => (
                            <li key={i}>{r}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </details>
                ))}
              </div>
            ) : (
              <div className="small-empty">
                <FileText size={21} />
                <p>No checkable evidence yet</p>
                <span>
                  Add expected output to run feedback, or create a case.
                </span>
                <Button
                  className="button-small"
                  icon={Plus}
                  onClick={() => setShowCase(true)}
                >
                  Add a case
                </Button>
              </div>
            )}
          </section>
          <section className="card">
            <div className="card-header">
              <div className="heading-icon">
                <GitBranch size={16} />
                <h3>Batch history</h3>
              </div>
            </div>
            {batches.length ? (
              <div className="batch-list">
                {batches.map((batch) => (
                  <button
                    className={cls(
                      "batch-row",
                      selected === batch.id && "selected",
                    )}
                    key={batch.id}
                    onClick={() => setSelected(batch.id)}
                  >
                    <div>
                      <strong>Batch {batchNumbers.get(batch.id)} · {shortId(batch.id)}</strong>
                      <small>{date(batch.created_at)}</small>
                    </div>
                    <Badge status={batch.status} />
                  </button>
                ))}
              </div>
            ) : (
              <p className="small-empty muted">
                Your first batch will appear here.
              </p>
            )}
          </section>
        </aside>
        <section className="card candidate-card">
          {selected ? (
            <BatchDetail
              key={selected}
              batchId={selected}
              agentId={agent.id}
              onError={onError}
              onRefresh={onRefresh}
            />
          ) : (
            <Empty icon={GitBranch} title="Make the next version measurable">
              Add a few evaluation cases, then generate a playbook candidate and
              compare it against the current agent.
              <div className="empty-steps">
                <span>Collect</span>
                <ArrowRight size={13} />
                <span>Evaluate</span>
                <ArrowRight size={13} />
                <span>Promote</span>
              </div>
            </Empty>
          )}
        </section>
      </div>
      {showCase && (
        <NewCaseModal
          agentId={agent.id}
          onClose={() => setShowCase(false)}
          onSaved={onRefresh}
          onError={onError}
        />
      )}
    </>
  );
}

function DeployView({ agent, onRefresh, onError }) {
  const [busy, setBusy] = useState(false);
  const [slug, setSlug] = useState(
    agent.deployment?.slug ||
      agent.name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, ""),
  );
  const [deployment, setDeployment] = useState(() =>
    agent.deployment
      ? {
          ...agent.deployment,
          token:
            sessionStorage.getItem("deployment-key:" + agent.deployment.slug) ||
            "",
        }
      : null,
  );
  const [rollback, setRollback] = useState("");
  useEffect(() => {
    if (agent.deployment)
      setDeployment((previous) => ({
        ...previous,
        ...agent.deployment,
        token: agent.deployment.token || previous?.token,
      }));
  }, [agent.deployment]);
  const absoluteUrl = deployment
    ? `${window.location.origin}/d/${deployment.slug}${deployment.token ? "?token=" + encodeURIComponent(deployment.token) : ""}`
    : "";
  async function deploy(versionId, rotateKey = false) {
    setBusy(true);
    try {
      const value = await post(`/agents/${agent.id}/deploy`, {
        slug,
        ...(versionId ? { version_id: versionId } : {}),
        ...(rotateKey ? { rotate_key: true } : {}),
      });
      const token =
        value.token ||
        deployment?.token ||
        sessionStorage.getItem("deployment-key:" + value.slug) ||
        "";
      if (token) sessionStorage.setItem("deployment-key:" + value.slug, token);
      setDeployment({ ...value, token });
      await onRefresh();
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const curl = deployment
    ? `curl -X POST '${window.location.origin}/api/deployments/${deployment.slug}/runs' \\\n  -H 'Content-Type: application/json' \\\n  -H 'X-Deployment-Key: ${deployment.token || "<your-deployment-key>"}' \\\n  -d '{"prompt":"Transform the attached data","files":[]}'`
    : "";
  const versions = agent.versions || [];
  return (
    <div className="deploy-grid">
      <div>
        <section className="card deploy-main">
          <div className="card-header">
            <div className="heading-icon">
              <Globe2 size={17} />
              <h3>Share your agent</h3>
            </div>
            {deployment && <Badge status="live">Live</Badge>}
          </div>
          <div className="card-body">
            <div className="deploy-illustration">
              <div className="deploy-agent-icon">
                <Bot size={30} strokeWidth={1.4} />
              </div>
              <span className="deploy-connection" />
              <div className="deploy-globe-icon">
                <Globe2 size={29} strokeWidth={1.4} />
              </div>
              <div>
                <strong>From your workspace to your team.</strong>
                <p>A stable URL and API, backed by a version you control.</p>
              </div>
            </div>
            <Field label="Deployment slug">
              <div className="slug-input">
                <span>/d/</span>
                <input
                  value={slug}
                  onChange={(e) =>
                    setSlug(
                      e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""),
                    )
                  }
                  placeholder="my-agent"
                />
              </div>
            </Field>
            {!deployment ? (
              <Button
                className="button-primary"
                icon={Rocket}
                busy={busy}
                disabled={!slug.trim()}
                onClick={() => deploy()}
              >
                Deploy {versionLabel(agent.current_version)}
              </Button>
            ) : (
              <>
                <div className="deployment-link">
                  <div>
                    <span className="eyebrow">SHARED RUN PAGE</span>
                    <a href={absoluteUrl} target="_blank" rel="noreferrer">
                      {window.location.host}/d/{deployment.slug}
                      <ArrowUpRight size={16} />
                    </a>
                  </div>
                  {deployment.token ? (
                    <CopyButton text={absoluteUrl} label="Copy link" />
                  ) : (
                    <Button
                      className="button-small"
                      busy={busy}
                      onClick={() => deploy(undefined, true)}
                    >
                      Generate access link
                    </Button>
                  )}
                </div>
                <div className="deploy-action-row">
                  <span>
                    <CheckCheck size={14} />
                    Published{" "}
                    {deployment.version_number
                      ? `v${deployment.version_number}`
                      : shortId(deployment.version_id)}
                  </span>
                  <Button
                    className="button-small"
                    icon={Rocket}
                    busy={busy}
                    onClick={() => deploy()}
                  >
                    Publish current version
                  </Button>
                </div>
                <p className="tiny-copy">
                  Publishing and rollback preserve this link. Generating a
                  replacement access link rotates the key. Share it only with
                  your intended users.
                </p>
              </>
            )}
          </div>
        </section>
        {deployment && (
          <section className="card api-card">
            <div className="card-header">
              <div className="heading-icon">
                <Code2 size={16} />
                <h3>Invoke from code</h3>
              </div>
              <CopyButton text={curl} />
            </div>
            <pre className="curl-code">{curl}</pre>
            <div className="api-footnote">
              <span className="method-tag">POST</span>
              <code>/api/deployments/{deployment.slug}/runs</code>
              <span>Returns a run ID immediately</span>
            </div>
          </section>
        )}
      </div>
      <aside>
        <section className="card deployment-safety">
          <div className="card-header">
            <div className="heading-icon">
              <ShieldCheck size={16} />
              <h3>Version control</h3>
            </div>
          </div>
          <div className="card-body">
            <div className="deploy-principle">
              <GitBranch size={17} />
              <div>
                <strong>Stable endpoint</strong>
                <p>
                  Promote a version without changing the URL your team uses.
                </p>
              </div>
            </div>
            <div className="deploy-principle">
              <Box size={17} />
              <div>
                <strong>Reproducible runs</strong>
                <p>
                  Each run keeps the exact version it started with, even after
                  promotion.
                </p>
              </div>
            </div>
            <div className="deploy-principle">
              <Waypoints size={17} />
              <div>
                <strong>Observable by default</strong>
                <p>
                  Deployed runs appear in your history, ready for feedback and
                  evaluation.
                </p>
              </div>
            </div>
            {deployment && versions.length > 1 && (
              <div className="rollback-controls">
                <Field label="Publish a previous version">
                  <select
                    value={rollback}
                    onChange={(e) => setRollback(e.target.value)}
                  >
                    <option value="">Select a version</option>
                    {[...versions].reverse().map((version) => (
                      <option key={version.id} value={version.id}>
                        {versionLabel(version)} · {date(version.created_at)}
                      </option>
                    ))}
                  </select>
                </Field>
                <Button
                  className="button-small"
                  icon={RefreshCw}
                  disabled={!rollback}
                  busy={busy}
                  onClick={() => deploy(rollback)}
                >
                  Publish selected version
                </Button>
              </div>
            )}
          </div>
        </section>
      </aside>
    </div>
  );
}

function Stats({ agent, runs }) {
  const stats = agent.stats || {};
  const total =
    stats.runs ?? stats.run_count ?? stats.total_runs ?? runs.length;
  const failed =
    stats.failed ??
    stats.failed_runs ??
    stats.execution_failures ??
    runs.filter((run) =>
      ["failed", "error", "interrupted"].includes(run.status),
    ).length;
  const labeled =
    stats.labeled ??
    stats.labeled_runs ??
    runs.filter((run) => ["passed", "failed"].includes(run.outcome)).length;
  const passed =
    stats.passed ??
    stats.passed_runs ??
    runs.filter((run) => run.outcome === "passed").length;
  const costs = runs.filter((run) => run.cost_usd != null);
  const cost = Object.hasOwn(stats, "cost_usd")
    ? stats.cost_usd
    : (stats.total_cost_usd ??
      (costs.length
        ? costs.reduce((sum, run) => sum + Number(run.cost_usd), 0)
        : null));
  return (
    <div className="stats-strip">
      <div>
        <span>
          <Activity size={13} />
          Total runs
        </span>
        <strong>
          {total}
          <small>team & playground runs</small>
        </strong>
      </div>
      <div>
        <span>
          <CheckCheck size={13} />
          Evaluated outcomes
        </span>
        <strong>
          {labeled ? (
            <>
              {passed}
              <i>/ {labeled}</i>
            </>
          ) : (
            "—"
          )}
          <small>{labeled ? "cases passed" : "no labeled outcomes yet"}</small>
        </strong>
      </div>
      <div>
        <span>
          <Zap size={13} />
          Model spend
        </span>
        <strong>
          {money(cost)}
          <small>
            {cost == null ? "usage appears after a run" : "recorded model cost"}
          </small>
        </strong>
      </div>
      <div>
        <span>
          <ShieldCheck size={13} />
          Execution health
        </span>
        <strong>
          {failed}
          <small>failed or interrupted runs</small>
        </strong>
      </div>
    </div>
  );
}

function SharedPage({ slug }) {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const [info, setInfo] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api(`/deployments/${slug}?token=${encodeURIComponent(token)}`)
      .then(setInfo)
      .catch((e) => setError(e.message));
  }, [slug, token]);
  return (
    <div className="shared-page">
      <header className="shared-header">
        <a className="shared-brand" href="/">
          <div className="brand-symbol">
            b<span />
          </div>
          brainbase
          <span className="brand-divider" />
          Shared agent
        </a>
        <span className="muted">
          <ShieldCheck size={14} />
          Secure deployment
        </span>
      </header>
      <main className="shared-main">
        <div className="shared-eyebrow">
          <span className="teal-dot status-dot" />
          DEPLOYED AGENT
        </div>
        <h1>{info?.agent_name || "Your team’s agent"}</h1>
        <p className="shared-description">
          Work with your agent across messages. Files stay with each conversation.
        </p>
        {error && (
          <div className="inline-error">
            <XCircle size={16} />
            {error}
          </div>
        )}
        {!info && !error ? (
          <Spinner label="Connecting to agent" />
        ) : info ? (
          <>
            <div className="shared-meta">
              <Badge status="live">Ready to run</Badge>
              <span>
                <GitBranch size={12} />
                {shortId(info.version_id)}
              </span>
            </div>
            <AgentChat slug={slug} token={token} onError={setError} latestVersionId={info.version_id}/>

          </>
        ) : null}
      </main>
      <footer className="shared-footer">
        <div className="brand-symbol small">
          b<span />
        </div>
        Powered by Brainbase<span>Each conversation has its own workspace.</span>
      </footer>
    </div>
  );
}

function App() {
  const initial = new URLSearchParams(window.location.search);
  const [agents, setAgents] = useState([]);
  const [agentId, setAgentId] = useState(initial.get("agent") || "");
  const [agent, setAgent] = useState(null);
  const [view, setView] = useState(
    TABS.some((tab) => tab.id === initial.get("view"))
      ? initial.get("view")
      : "build",
  );
  const [selectedRun, setSelectedRun] = useState(initial.get("run") || null);
  const [selectedBatch, setSelectedBatch] = useState(null);
  const [runs, setRuns] = useState([]);
  const [cases, setCases] = useState([]);
  const [batches, setBatches] = useState([]);
  const [status, setStatus] = useState({});
  const [models, setModels] = useState([]);
  const [defaultModel, setDefaultModel] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [running, setRunning] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showSetup, setShowSetup] = useState(false);
  const notifyError = useCallback((message) => setError(message), []);
  const currentAgent = useRef(agentId);
  currentAgent.current = agentId;
  const loadAgents = useCallback(async () => {
    const values = await api("/agents");
    const list = Array.isArray(values) ? values : values.agents || [];
    setAgents(list);
    if (!currentAgent.current && list.length) setAgentId(list[0].id);
    return list;
  }, []);
  const refresh = useCallback(async () => {
    const id = currentAgent.current;
    if (!id) return;
    const settled = await Promise.allSettled([
      api(`/agents/${id}`),
      api(`/agents/${id}/runs`),
      api(`/agents/${id}/cases`),
      api(`/agents/${id}/improvements`),
    ]);
    if (currentAgent.current !== id) return;
    const setters = [setAgent, setRuns, setCases, setBatches];
    const keys = ["", "runs", "cases", "improvements"];
    settled.forEach((result, index) => {
      if (result.status === "fulfilled") {
        const value = result.value;
        setters[index](
          index && !Array.isArray(value) ? value[keys[index]] || [] : value,
        );
      } else if (index === 0) setError(result.reason.message);
    });
  }, []);
  useEffect(() => {
    let alive = true;
    Promise.allSettled([loadAgents(), api("/status"), api("/models")]).then(
      (values) => {
        if (!alive) return;
        if (values[0].status === "rejected") setError(values[0].reason.message);
        if (values[1].status === "fulfilled") setStatus(values[1].value);
        if (values[2].status === "fulfilled") {
          setModels(values[2].value.models || []);
          setDefaultModel(values[2].value.default_model || "");
        }
        setLoading(false);
      },
    );
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    setAgent(null);
    setRuns([]);
    setCases([]);
    setBatches([]);
    if (agentId) refresh();
  }, [agentId]);
  usePolling(() => {
    loadAgents().catch(() => {});
    if (currentAgent.current) refresh();
    api("/status")
      .then(setStatus)
      .catch(() => {});
  }, 4000);
  useEffect(() => {
    const params = new URLSearchParams();
    if (agentId) params.set("agent", agentId);
    if (view !== "build") params.set("view", view);
    if (selectedRun && view === "runs") params.set("run", selectedRun);
    window.history.replaceState(
      {},
      "",
      window.location.pathname + (params.size ? "?" + params : ""),
    );
  }, [agentId, view, selectedRun]);
  useEffect(() => {
    const open = (event) => {
      setSelectedRun(event.detail);
      setView("runs");
    };
    window.addEventListener("open-run", open);
    const openBatch = (event) => { setSelectedBatch(event.detail); setView('improve'); };
    window.addEventListener('open-batch', openBatch);
    return () => { window.removeEventListener("open-run", open); window.removeEventListener('open-batch', openBatch); };
  }, []);
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(""), 3500);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  async function run(body) {
    setRunning(true);
    setError("");
    try {
      const value = await post(`/agents/${agentId}/runs`, body);
      setSelectedRun(value.id);
      setView("runs");
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }
  async function seed() {
    setSeeding(true);
    setError("");
    try {
      const value = await post("/demo/seed");
      const id = value.id || value.agent?.id || value.agent_id;
      await loadAgents();
      if (id) setAgentId(id);
      setView("build");
      setToast("Demo agent and evaluation cases are ready.");
    } catch (e) {
      setError(e.message);
    } finally {
      setSeeding(false);
    }
  }
  function chooseAgent(id) {
    setAgentId(id);
    setSelectedRun(null);
    setSidebarOpen(false);
    setError("");
  }
  return (
    <div className="app-shell">
      <aside className={cls("sidebar", sidebarOpen && "sidebar-open")}>
        <a className="brand" href="/">
          <div className="brand-symbol">
            b<span />
          </div>
          <span>brainbase</span>
          <span className="brand-beta">LABS</span>
        </a>
        <div className="workspace-selector">
          <div className="workspace-avatar">W</div>
          <div>
            <strong>My workspace</strong>
            <span>Agent platform</span>
          </div>
          <ChevronDown size={14} />
        </div>
        <div className="sidebar-section-label">WORKSPACE</div>
        <button
          className="sidebar-nav selected"
          onClick={() => setSidebarOpen(false)}
        >
          <Layers3 size={17} />
          <span>Agents</span>
          <span className="sidebar-count">{agents.length}</span>
        </button>
        <div className="sidebar-agent-heading">
          <span>YOUR AGENTS</span>
          <button
            aria-label="Create an agent"
            onClick={() => setShowCreate(true)}
          >
            <Plus size={16} />
          </button>
        </div>
        <div className="sidebar-agent-list">
          {agents.map((item) => (
            <button
              className={cls("sidebar-agent", agentId === item.id && "active")}
              onClick={() => chooseAgent(item.id)}
              key={item.id}
            >
              <Bot size={16} />
              <span>{item.name}</span>
              {item.deployment && <span className="agent-live-dot" />}
            </button>
          ))}
          {!agents.length && (
            <span className="sidebar-empty">Your agents will live here.</span>
          )}
        </div>
        <button className="sidebar-new" onClick={() => setShowCreate(true)}>
          <Plus size={15} />
          New agent
        </button>
        <div className="sidebar-bottom">
          <div className="learning-note">
            <div>
              <Waypoints size={17} />
              <span>Built to improve.</span>
            </div>
            <p>Every trace is a chance to make your agents better.</p>
          </div>
          <div className="sidebar-user">
            <div className="user-avatar">AK</div>
            <div>
              <strong>Personal workspace</strong>
              <span>Local development</span>
            </div>
            <MoreHorizontal size={17} />
          </div>
        </div>
      </aside>
      {sidebarOpen && (
        <div className="sidebar-scrim" onClick={() => setSidebarOpen(false)} />
      )}
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              className="mobile-menu icon-button"
              aria-label="Open navigation"
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              <Layers3 size={18} />
            </button>
            <Layers3 size={15} />
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>Agents</strong>
            {agent && (
              <>
                <ChevronRight size={13} />
                <span>{agent.name}</span>
              </>
            )}
          </div>
          <div className="system-status">
            <span
              className={cls(
                "status-dot",
                status.ready && "teal-dot",
                status.ready === false && "amber-dot",
              )}
            />
            <span>
              {status.ready
                ? "Runtime ready"
                : status.ready === false
                  ? "Setup required"
                  : "Checking runtime"}
            </span>
            <span className="topbar-divider" />
            <span className="environment-tag">LOCAL</span>
          </div>
        </header>
        <main className="workspace-content">
          {error && (
            <div className="error-banner" role="alert">
              <XCircle size={17} />
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={15} />
              </button>
            </div>
          )}
          {status.ready === false && (
            <div className="setup-banner">
              <div className="setup-banner-icon">
                <SquareTerminal size={18} />
              </div>
              <div>
                <strong>Connect your runtime to start running agents.</strong>
                <p>
                  {status.reason ||
                    "Configure a model provider key and a sandbox, then restart the backend."}
                </p>
              </div>
              <Button
                className="button-small"
                icon={Settings2}
                onClick={() => setShowSetup(true)}
              >
                Setup instructions
              </Button>
            </div>
          )}
          {loading ? (
            <Spinner />
          ) : agentId && !agent ? (
            <Spinner label="Loading agent" />
          ) : agent ? (
            <>
              <div className="page-heading">
                <div className="agent-title-icon">
                  <Bot size={27} strokeWidth={1.5} />
                  <span className="title-icon-dot" />
                </div>
                <div className="agent-title">
                  <div className="agent-title-row">
                    <h1>{agent.name}</h1>
                    {agent.deployment ? (
                      <Badge status="live">Deployed</Badge>
                    ) : (
                      <Badge>Draft</Badge>
                    )}
                  </div>
                  <p>
                    {agent.description ||
                      "Your team’s agent, with every run in view."}
                  </p>
                </div>
                <Button
                  className="button-new-run"
                  icon={Plus}
                  onClick={() => setView("build")}
                >
                  Open chat
                </Button>
              </div>
              <Stats agent={agent} runs={runs} />
              <div className="main-tabs">
                {TABS.map(({ id, label, icon: Icon }) => (
                  <button
                    className={view === id ? "active" : ""}
                    onClick={() => setView(id)}
                    key={id}
                  >
                    <Icon size={16} />
                    {label}
                    {id === "runs" && runs.length > 0 && (
                      <span>{runs.length}</span>
                    )}
                    {id === "improve" &&
                      batches.some(
                        (batch) => batch.status === "awaiting_review",
                      ) && <span className="tab-notification" />}
                  </button>
                ))}
                <div className="tabs-right">
                  <GitBranch size={13} />
                  {versionLabel(agent.current_version)}
                </div>
              </div>
              <div className="view-content">
                {view === "build" && (
                  <BuildView
                    key={agent.id}
                    agent={agent}
                    models={models}
                    status={status}
                    onRefresh={refresh}
                    onError={notifyError}
                    onRun={run}
                    running={running}
                  />
                )}
                {view === "runs" && (
                  <RunsView
                    agent={agent}
                    runs={runs}
                    selectedRun={selectedRun}
                    onSelect={setSelectedRun}
                    onError={notifyError}
                    onRefresh={refresh}
                  />
                )}
                {view === "evals" && (
                  <EvalsView
                    key={agent.id}
                    agent={agent}
                    cases={cases}
                    onRefresh={refresh}
                    onError={notifyError}
                  />
                )}
                {view === "improve" && (
                  <ImproveView
                    key={agent.id}
                    initialBatchId={batches.some(b => b.id === selectedBatch) ? selectedBatch : null}
                    agent={agent}
                    cases={cases}
                    batches={batches}
                    onRefresh={refresh}
                    onError={notifyError}
                  />
                )}
                {view === "deploy" && (
                  <DeployView
                    key={agent.id}
                    agent={agent}
                    onRefresh={refresh}
                    onError={notifyError}
                  />
                )}
              </div>
            </>
          ) : (
            <div className="onboarding">
              <div className="onboarding-top">
                <span className="eyebrow">YOUR AGENT WORKSPACE</span>
                <Badge>START BUILDING</Badge>
              </div>
              <div className="onboarding-graphic">
                <div className="orbit orbit-one" />
                <div className="orbit orbit-two" />
                <span className="orbit-dot dot-one" />
                <span className="orbit-dot dot-two" />
                <span className="orbit-label orbit-label-left">
                  <SquareTerminal size={15} />
                  Code & file tools
                </span>
                <span className="orbit-label orbit-label-right">
                  <Waypoints size={15} />
                  Every trace, recorded
                </span>
                <div className="onboarding-bot">
                  <Bot size={54} strokeWidth={1.2} />
                </div>
              </div>
              <h1>
                Build an agent.
                <br />
                <span>Give it room to get better.</span>
              </h1>
              <p>
                Create an agent for your team, follow every step it takes,
                <br className="desktop-break" /> and turn real feedback into a
                tested next version.
              </p>
              <div className="onboarding-actions">
                <Button
                  className="button-primary"
                  icon={Plus}
                  onClick={() => setShowCreate(true)}
                >
                  Create your first agent
                </Button>
                <Button icon={Beaker} busy={seeding} onClick={seed}>
                  Explore the demo
                </Button>
              </div>
              <div className="onboarding-bottom">
                {[
                  {
                    icon: Bot,
                    title: "Create & run",
                    text: "Instructions, models, and sandboxed tools.",
                  },
                  {
                    icon: Waypoints,
                    title: "Observe & correct",
                    text: "Understand the run. Capture the lesson.",
                  },
                  {
                    icon: Sparkles,
                    title: "Improve & deploy",
                    text: "Measure each change before it goes live.",
                  },
                ].map(({ icon: Icon, title, text }, i) => (
                  <div key={title}>
                    <span className="onboarding-step">0{i + 1}</span>
                    <Icon size={17} />
                    <strong>{title}</strong>
                    <p>{text}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </main>
        <footer className="workspace-footer">
          <span>
            <span className="status-dot teal-dot" />
            All traces persisted locally
          </span>
          <span>Built with intent. Improved with evidence.</span>
        </footer>
      </div>
      {showCreate && (
        <CreateAgentModal
          models={models}
          defaultModel={defaultModel}
          onClose={() => setShowCreate(false)}
          onError={notifyError}
          onCreated={async (value) => {
            setShowCreate(false);
            await loadAgents();
            chooseAgent(value.id);
            setView("build");
            setToast("Agent created. Give it its first task.");
          }}
        />
      )}
      {showSetup && (
        <Modal
          title="Connect the runtime"
          subtitle="Add a provider key locally. Credentials stay on the API server."
          onClose={() => setShowSetup(false)}
        >
          <div className="setup-instructions">
            <div>
              <span className="setup-number">1</span>
              <div>
                <h3>Configure a model provider</h3>
                <p>
                  Copy <code>.env.example</code> to <code>.env</code> in the
                  project root. Set <code>MODEL_PROVIDER</code> to{" "}
                  <code>openrouter</code> or <code>openai</code>, then fill in
                  the matching API key.
                </p>
                <pre>
                  MODEL_PROVIDER=openrouter{"\n"}OPENROUTER_API_KEY=your-key
                  {"\n"}DEFAULT_MODEL=openai/gpt-4.1-mini
                </pre>
              </div>
            </div>
            <div>
              <span className="setup-number">2</span>
              <div>
                <h3>Start the code sandbox</h3>
                <p>Open Docker Desktop, then pull the sandbox image once.</p>
                <pre>docker pull python:3.12-slim</pre>
              </div>
            </div>
            <div>
              <span className="setup-number">3</span>
              <div>
                <h3>Confirm the connection</h3>
                <p>
                  New keys in <code>.env</code> are picked up on the next
                  request. Restart the backend when changing an existing
                  setting. Runtime status refreshes here automatically.
                </p>
              </div>
            </div>
            <div className="setup-current">
              <span
                className={cls(
                  "status-dot",
                  status.ready ? "teal-dot" : "amber-dot",
                )}
              />
              {status.ready
                ? "Runtime is ready"
                : status.reason || "Waiting for configuration"}
            </div>
            <div className="modal-footer">
              <Button
                className="button-primary"
                icon={Check}
                onClick={() => setShowSetup(false)}
              >
                Got it
              </Button>
            </div>
          </div>
        </Modal>
      )}
      {toast && (
        <div className="toast">
          <CheckCheck size={17} />
          {toast}
        </div>
      )}
    </div>
  );
}

const deploymentMatch = window.location.pathname.match(/^\/d\/([^/]+)/);
createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    {deploymentMatch ? (
      <SharedPage slug={decodeURIComponent(deploymentMatch[1])} />
    ) : (
      <App />
    )}
  </React.StrictMode>,
);
