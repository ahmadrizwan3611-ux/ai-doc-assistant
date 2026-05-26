import React, { useState, useEffect, useMemo, useRef } from "react";
import "./App.css";
import jsPDF from "jspdf";
import toast, { Toaster } from "react-hot-toast";
import { InfinitySpin } from "react-loader-spinner";

const API_BASE_URL = process.env.REACT_APP_API_URL || ((typeof window !== "undefined" && (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")) ? "http://127.0.0.1:5000" : window.location.origin);

// Public demo mode: visitors can test DevFlow without login, signup, pricing, or subscriptions.
const PUBLIC_DEMO_MODE = true;
const DEMO_USER = { id: "public-demo-user", email: "demo@devflow.local", full_name: "DevFlow Demo User", demo: true };
const DEMO_WORKSPACE = { id: "public-demo-workspace", name: "Public Live Demo", role: "demo", demo: true };

const formatPlanName = (plan) => {
  const value = String(plan || "free").toLowerCase();
  if (value === "pro") return "PRO";
  if (value === "team") return "TEAM";
  return "FREE";
};


const formatDocumentationMode = (mode) => {
  const value = String(mode || "").toLowerCase();
  if (value === "pasted_snippet") return "Code Snippet";
  if (value === "single_file") return "Single File";
  if (value === "multi_file") return "Multi-file";
  if (value === "full_project") return "Full Project";
  if (value === "github_repo" || value.includes("repo")) return "GitHub Repo";
  return "Smart Docs";
};


const cleanDevFlowOutput = (text) => {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/```(?:json|python|javascript|js|jsx|ts|tsx|bash|shell|html|css|markdown|md)?/gi, "")
    .replace(/```/g, "")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s*#{1,6}\s*/gm, "")
    .replace(/^\s*[-=]{6,}\s*$/gm, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
};


const DEVFLOW_SECTION_HEADINGS = new Set([
  "executive summary",
  "overview",
  "file purpose",
  "project purpose",
  "what this project does",
  "technology stack",
  "technology detected",
  "architecture overview",
  "important files",
  "important functions",
  "important functions and classes",
  "function and method explanations",
  "main workflows",
  "api routes",
  "routes if any",
  "important logic",
  "dependencies",
  "security observations",
  "security risks",
  "risks",
  "suggested improvements",
  "improvement roadmap",
  "developer handover notes",
  "how to run",
  "how to run / setup",
  "setup notes",
  "large file summary",
  "detected functions",
  "why this score",
  "detected frameworks",
  "detected routes",
  "architecture notes",
  "issues",
  "priority fixes",
  "testing notes",
  "what the error means",
  "why it happened",
  "likely cause",
  "step-by-step fix",
  "fixed version",
  "prevention",
  "summary",
  "implementation notes",
  "subtasks",
  "acceptance criteria",
  "definition of done",
  "qa notes",
]);

const normalizeSectionKey = (line) =>
  String(line || "")
    .replace(/^#+\s*/, "")
    .replace(/[:：]\s*$/, "")
    .trim()
    .toLowerCase();

const isDevFlowSectionHeading = (line) => {
  const value = String(line || "").trim();
  if (!value) return false;

  const normalized = normalizeSectionKey(value);
  if (DEVFLOW_SECTION_HEADINGS.has(normalized)) return true;
  if (/^task\s+\d+\s*:/i.test(value)) return true;
  if (/^(phase|step)\s+\d+/i.test(value) && value.length < 90) return true;

  return (
    value.length <= 64 &&
    !/^[-*•]\s+/.test(value) &&
    !/^\d+\./.test(value) &&
    !/[.;]$/.test(value) &&
    !/https?:\/\//i.test(value) &&
    !/^\w+\s*:\s+.{18,}/.test(value) &&
    /^[A-Z][A-Za-z0-9 /&()+-]+$/.test(value)
  );
};

const isMetaLine = (line) => {
  const value = String(line || "").trim();
  return /^[A-Za-z][A-Za-z0-9 /&()+-]{1,36}:\s+.+$/.test(value) && value.length <= 150;
};

const renderInlineText = (text) => {
  const parts = String(text || "").split(/(\b[A-Za-z0-9_./-]+\.(?:py|js|jsx|ts|tsx|css|html|json|md|txt|sql|env)\b|\/[A-Za-z0-9_./-]+|POST\s+\/[A-Za-z0-9_./-]+|GET\s+\/[A-Za-z0-9_./-]+)/g);
  return parts.map((part, index) => {
    if (/(\.(py|js|jsx|ts|tsx|css|html|json|md|txt|sql|env)\b|^(POST|GET)\s+\/|^\/[A-Za-z0-9_./-]+)/i.test(part)) {
      return <code key={index}>{part}</code>;
    }
    return <React.Fragment key={index}>{part}</React.Fragment>;
  });
};

function DevFlowDocumentView({ content, emptyText }) {
  const prepared = cleanDevFlowOutput(content);
  const lines = prepared.split("\n").map((line) => line.trim()).filter(Boolean);

  if (!prepared) {
    return (
      <div className="output-box document-view empty-output">
        <div>{emptyText}</div>
      </div>
    );
  }

  const blocks = [];
  let listItems = [];

  const flushList = () => {
    if (listItems.length) {
      blocks.push(
        <ul className="doc-list" key={`list-${blocks.length}`}>
          {listItems.map((item, index) => (
            <li key={index}>{renderInlineText(item)}</li>
          ))}
        </ul>
      );
      listItems = [];
    }
  };

  lines.forEach((line, index) => {
    const bullet = line.match(/^[-*•]\s+(.+)$/);
    if (bullet) {
      listItems.push(bullet[1]);
      return;
    }

    flushList();

    if (isDevFlowSectionHeading(line)) {
      blocks.push(
        <h3 className="doc-section-title" key={`heading-${index}`}>
          {renderInlineText(line.replace(/^#+\s*/, ""))}
        </h3>
      );
      return;
    }

    if (isMetaLine(line)) {
      const splitIndex = line.indexOf(":");
      blocks.push(
        <div className="doc-meta-row" key={`meta-${index}`}>
          <span className="doc-meta-key">{line.slice(0, splitIndex)}</span>
          <span className="doc-meta-value">{renderInlineText(line.slice(splitIndex + 1).trim())}</span>
        </div>
      );
      return;
    }

    blocks.push(
      <p className="doc-paragraph" key={`paragraph-${index}`}>
        {renderInlineText(line)}
      </p>
    );
  });

  flushList();

  return <div className="output-box document-view">{blocks}</div>;
}

const FILE_PRIORITY_RULES = [
  { test: (p) => /(^|\/)(package\.json|requirements\.txt|procfile|railway\.json|dockerfile|vite\.config\.[jt]s|next\.config\.[jt]s)$/i.test(p), score: 100 },
  { test: (p) => /(^|\/)(app\.py|main\.py|server\.js|index\.js|manage\.py)$/i.test(p), score: 95 },
  { test: (p) => /(^|\/)(App\.(js|jsx|ts|tsx)|App\.css|index\.html)$/i.test(p), score: 90 },
  { test: (p) => /(auth|billing|stripe|supabase|github|workspace|document|route|api)/i.test(p), score: 82 },
  { test: (p) => /\/src\/|\/pages\/|\/api\/|\/routes\/|\/components\//i.test(p), score: 70 },
  { test: (p) => /\.(py|js|jsx|ts|tsx)$/i.test(p), score: 55 },
  { test: (p) => /\.(css|html|json|md)$/i.test(p), score: 40 },
];

const scoreProjectFile = (fileName) => {
  const path = String(fileName || "").replace(/\\/g, "/");
  const matched = FILE_PRIORITY_RULES.find((rule) => rule.test(path));
  return matched ? matched.score : 10;
};

const getExtensionLanguage = (fileName) => {
  const name = String(fileName || "").toLowerCase();
  if (name.endsWith(".py")) return "Python";
  if (name.endsWith(".js") || name.endsWith(".jsx")) return "JavaScript / React";
  if (name.endsWith(".ts") || name.endsWith(".tsx")) return "TypeScript / React";
  if (name.endsWith(".css")) return "CSS";
  if (name.endsWith(".html")) return "HTML";
  if (name.endsWith(".json")) return "JSON";
  if (name.endsWith(".md")) return "Markdown";
  if (name.endsWith(".sql")) return "SQL";
  return "Text";
};

const extractImportantCodeLines = (content) => {
  const lines = String(content || "").split("\n");
  const important = lines.filter((raw) => {
    const line = raw.trim();
    return (
      line.startsWith("import ") ||
      line.startsWith("from ") ||
      line.startsWith("export ") ||
      line.startsWith("@app.route") ||
      line.startsWith("def ") ||
      line.startsWith("class ") ||
      line.startsWith("function ") ||
      line.startsWith("const ") ||
      line.startsWith("let ") ||
      line.startsWith("var ") ||
      line.includes("fetch(") ||
      line.includes("app.route") ||
      line.includes("createClient") ||
      line.includes("stripe.") ||
      line.includes("supabase")
    );
  });

  return important.slice(0, 90).join("\n");
};

const compactSourceFile = (file, index) => {
  const name = file.name || `file-${index + 1}`;
  const content = String(file.content || "");
  const lines = content.split("\n");
  const priority = scoreProjectFile(name);
  const language = getExtensionLanguage(name);
  const importantLines = extractImportantCodeLines(content);
  // Tighter limits when many files are present — keeps total under 150k for 50+ file projects
  const fullLimit = priority >= 82 ? 8000 : priority >= 55 ? 3500 : 1500;

  if (content.length <= fullLimit) {
    return `--- FILE: ${name} ---\nLANGUAGE: ${language}\nTOTAL_LINES: ${lines.length}\nPRIORITY: ${priority}\n${content}`;
  }

  const head = lines.slice(0, priority >= 82 ? 80 : 35).join("\n");
  const tail = lines.slice(priority >= 82 ? -25 : -12).join("\n");

  return [
    `--- FILE: ${name} ---`,
    `LANGUAGE: ${language}`,
    `TOTAL_LINES: ${lines.length}`,
    `PRIORITY: ${priority}`,
    "COMPACTED_FOR_SMART_PROJECT_MODE: yes",
    "",
    "IMPORTANT_SIGNATURES_AND_ROUTES:",
    importantLines || "- No signatures, routes, or imports detected in compact summary.",
    "",
    "FILE_START_EXCERPT:",
    head,
    "",
    "FILE_END_EXCERPT:",
    tail,
  ].join("\n");
};

const buildSmartUploadPackage = (files, selectedCount) => {
  const normalizedFiles = files
    .map((file, index) => ({ ...file, index, score: scoreProjectFile(file.name) }))
    .sort((a, b) => b.score - a.score || a.name.localeCompare(b.name));

  const hasProjectStructure =
    normalizedFiles.length >= 8 ||
    normalizedFiles.some((file) => String(file.name || "").includes("/")) ||
    normalizedFiles.some((file) => /(^|\/)(package\.json|requirements\.txt|procfile|railway\.json|app\.py|App\.js)$/i.test(file.name || ""));

  const totalLines = normalizedFiles.reduce((sum, file) => sum + String(file.content || "").split("\n").length, 0);
  const manifest = normalizedFiles
    .map((file) => `- ${file.name} (${getExtensionLanguage(file.name)}, ${String(file.content || "").split("\n").length} lines, priority ${file.score})`)
    .join("\n");

  const header = [
    "DEVFLOW SMART UPLOAD CONTEXT",
    `Input Type: ${hasProjectStructure ? "Full Project" : normalizedFiles.length === 1 ? "Single File" : "Multi-file Upload"}`,
    `Files selected by user: ${selectedCount || normalizedFiles.length}`,
    `Files included after filtering: ${normalizedFiles.length}`,
    `Total visible lines before compaction: ${totalLines}`,
    "Instruction: Generate one organized professional report for the whole input. Do not explain every file separately unless it is important.",
    "Instruction: If this is a full project, explain purpose, architecture, tech stack, main workflows, important files, routes, risks, and improvements.",
    "",
    "PROJECT FILE MANIFEST",
    manifest,
    "",
    "IMPORTANT SOURCE CONTEXT",
  ].join("\n");

  const chunks = [header];
  let usedChars = header.length;
  let omitted = 0;

  normalizedFiles.forEach((file, index) => {
    const compacted = compactSourceFile(file, index);
    if (usedChars + compacted.length + 4 > MAX_BACKEND_CODE_CHARS) {
      omitted += 1;
      return;
    }
    chunks.push(compacted);
    usedChars += compacted.length + 4;
  });

  if (omitted > 0) {
    chunks.push(
      [
        "",
        "SMART_COMPACTION_NOTE",
        `${omitted} lower-priority files were summarized or omitted from the live AI request to keep the server request safe.`,
        "The file manifest still lists the project structure, so the documentation should focus on architecture and important files.",
      ].join("\n")
    );
  }

  return {
    code: chunks.join("\n\n"),
    mode: hasProjectStructure ? "full_project" : normalizedFiles.length === 1 ? "single_file" : "multi_file",
    omitted,
  };
};

const prepareCodeForRequest = (rawCode, rawFileName = "") => {
  const value = String(rawCode || "");
  if (value.length <= MAX_BACKEND_CODE_CHARS) return value;

  const fileBlocks = value
    .split(/\n(?=--- FILE:\s)/g)
    .map((block) => {
      const match = block.match(/^--- FILE:\s*(.*?)\s*---\n?([\s\S]*)$/);
      if (!match) return null;
      return { name: match[1].trim(), content: match[2] || "" };
    })
    .filter(Boolean);

  if (fileBlocks.length) {
    return buildSmartUploadPackage(fileBlocks, fileBlocks.length).code;
  }

  return [
    "DEVFLOW SMART UPLOAD CONTEXT",
    "Input Type: Large Pasted Code",
    `Original length: ${value.length} characters`,
    rawFileName ? `File name: ${rawFileName}` : "",
    "Instruction: The pasted code was too large, so this request contains the most important visible part. Explain purpose, important functions, risks, and improvements.",
    "",
    value.slice(0, MAX_BACKEND_CODE_CHARS - 1200),
    "",
    "SMART_COMPACTION_NOTE",
    "The remaining pasted content was trimmed client-side to prevent a server size error.",
  ].filter(Boolean).join("\n");
};


const listToText = (items, fallback = "- None") => {
  if (!Array.isArray(items) || items.length === 0) return fallback;
  return items.map((item) => "- " + cleanDevFlowOutput(item)).join("\n");
};

const formatGeneratedTasks = (tasks) => {
  if (!Array.isArray(tasks) || tasks.length === 0) return "No tasks returned.";

  return tasks.map((task, index) => {
    const title = cleanDevFlowOutput(task.title || `Task ${index + 1}`);
    const sections = [
      `Task ${index + 1}: ${title}`,
      "",
      `Summary: ${cleanDevFlowOutput(task.summary || "No summary provided.")}`,
      `Priority: ${cleanDevFlowOutput(task.priority || "Medium")}`,
      `Role: ${cleanDevFlowOutput(task.role || "Full Stack Developer")}`,
      `Feature Area: ${cleanDevFlowOutput(task.feature_area || "General")}`,
      `Estimated Time: ${cleanDevFlowOutput(task.estimated_time || "Not estimated")}`,
    ];

    if (task.user_story) {
      sections.push("", "User Story", cleanDevFlowOutput(task.user_story));
    }

    sections.push(
      "",
      "Implementation Notes",
      listToText(task.implementation_notes),
      "",
      "Subtasks",
      listToText(task.subtasks),
      "",
      "Acceptance Criteria",
      listToText(task.acceptance_criteria),
      "",
      "Dependencies",
      listToText(task.dependencies),
      "",
      "QA Notes",
      listToText(task.qa_notes),
      "",
      "Definition of Done",
      listToText(task.definition_of_done)
    );

    return sections.join("\n");
  }).join("\n\n----------------------------------------\n\n");
};

const formatHealthReport = (rp) => {
  if (!rp) return "No health review returned.";

  const reviewTitle = cleanDevFlowOutput(rp.review_type || "Smart Health Review");
  const scoreLabel = cleanDevFlowOutput(rp.score_label || "Health Score");
  const readiness = cleanDevFlowOutput(rp.production_readiness || "Not applicable");
  const scopeLabel = cleanDevFlowOutput(rp.scope || "unknown");

  const sections = [
    reviewTitle,
    "",
    `Scope: ${scopeLabel}`,
    rp.scope_note ? `Scope Note: ${cleanDevFlowOutput(rp.scope_note)}` : "",
    `${scoreLabel}: ${cleanDevFlowOutput(rp.score || "Not scored")}`,
  ].filter(Boolean);

  if (readiness && readiness.toLowerCase() !== "not applicable") {
    sections.push(`Production Readiness: ${readiness}`);
  } else {
    sections.push("Production Readiness: Not applicable for this input scope");
  }

  sections.push(
    `Total Files Reviewed: ${rp.total_files_detected || 0}`,
    "",
    "Why This Score",
    listToText(rp.score_explanation),
    "",
    "Tech Stack",
    listToText(rp.tech_stack, "- Not detected"),
    "",
    "Detected Frameworks",
    listToText(rp.detected_frameworks),
    "",
    "Detected Routes",
    listToText(rp.routes, "- No routes detected in this input"),
    "",
    "Important Files",
    listToText(rp.important_files),
    "",
    "Architecture Notes",
    listToText(rp.architecture_notes),
    "",
    "Issues",
    listToText(rp.issues, "- No major issues detected"),
    "",
    "Security Risks",
    listToText(rp.security_risks, "- No concrete security risks detected"),
    "",
    "Priority Fixes",
    listToText(rp.priority_fixes),
    "",
    "Suggestions",
    listToText(rp.suggestions),
    "",
    "Testing Notes",
    listToText(rp.testing_notes)
  );

  return sections.join("\n");
};



const MAX_FILE_SIZE_MB = 8;
const MAX_TOTAL_FILES = 120;
// Increased from 85k — server-side smart compaction handles 50+ file projects safely
const MAX_BACKEND_CODE_CHARS = 150000;

// Auth helpers 
const getToken = () => localStorage.getItem("devflow_token");
const getRefreshToken = () => localStorage.getItem("devflow_refresh_token");
const getStoredUser = () => {
 try {
 return JSON.parse(localStorage.getItem("devflow_user"));
 } catch {
 return null;
 }
};
const setSession = ({ access_token, refresh_token, user }) => {
 if (access_token) localStorage.setItem("devflow_token", access_token);
 if (refresh_token) localStorage.setItem("devflow_refresh_token", refresh_token);
 if (user) localStorage.setItem("devflow_user", JSON.stringify(user));
};
const clearSession = () => {
 localStorage.removeItem("devflow_token");
 localStorage.removeItem("devflow_refresh_token");
 localStorage.removeItem("devflow_user");
};
const authHeaders = () => {
 const token = getToken();
 return {
 "Content-Type": "application/json",
 ...(token ? { Authorization: `Bearer ${token}` } : {})
 };
};

const safeJson = async (response) => {
 try {
 return await response.json();
 } catch {
 return {};
 }
};

const refreshSession = async () => {
 const refreshToken = getRefreshToken();
 if (!refreshToken) return null;

 const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
 method: "POST",
 headers: { "Content-Type": "application/json" },
 body: JSON.stringify({ refresh_token: refreshToken }),
 });
 const data = await safeJson(response);
 if (!response.ok || data.error || !data.access_token) {
 clearSession();
 return null;
 }
 setSession(data);
 return data;
};

const authFetch = async (path, options = {}, onAuthExpired) => {
 const makeRequest = () => fetch(`${API_BASE_URL}${path}`, {
 ...options,
 headers: {
 ...authHeaders(),
 ...(options.headers || {}),
 },
 });

 let response = await makeRequest();
 if (response.status !== 401) return response;

 const refreshed = await refreshSession();
 if (!refreshed) {
 if (onAuthExpired) onAuthExpired();
 return response;
 }

 response = await makeRequest();
 if (response.status === 401 && onAuthExpired) {
 clearSession();
 onAuthExpired();
 }
 return response;
};


// 
// SAAS LANDING PAGE PUBLIC DEMO
// 
function LandingPage({ onStart }) {
  return (
    <div className="landing-page public-demo-landing">
      <header className="landing-nav">
        <div>
          <h1 className="landing-logo">DevFlow</h1>
          <p className="landing-subtitle">AI-powered developer workspace</p>
        </div>

        <button
          type="button"
          className="landing-primary-btn"
          onClick={onStart}
        >
          Open Demo Workspace
        </button>
      </header>

      <main className="landing-hero">
        <div className="landing-pill">
          AI-powered developer workspace for software teams
        </div>

        <h2>
          Turn any codebase into docs, tasks, health reports, and team knowledge.
        </h2>

        <p>
          DevFlow helps developers and small teams upload code or project folders,
          generate clean documentation, analyze bugs, review project health, create
          task plans, and export reports.
        </p>

        <button
          type="button"
          className="landing-main-btn"
          onClick={onStart}
        >
          Open Demo Workspace
        </button>
      </main>
    </div>
  );
}

// 
// AUTH SCREEN
// 
function AuthScreen({ onLogin, initialMode = "login", onBackToLanding }) {
 const [mode, setMode] = useState(initialMode); // "login" | "signup"
 const [email, setEmail] = useState("");
 const [password, setPass] = useState("");
 const [fullName, setName] = useState("");
 const [loading, setLoading] = useState(false);

 useEffect(() => {
 setMode(initialMode);
 }, [initialMode]);

 const handle = async () => {
 if (!email || !password) { toast.error("Email and password required."); return; }
 setLoading(true);
 try {
 const endpoint = mode === "login" ? "/auth/login" : "/auth/signup";
 const body = mode === "login"
 ? { email, password }
 : { email, password, full_name: fullName };

 const r = await fetch(`${API_BASE_URL}${endpoint}`, {
 method: "POST",
 headers: { "Content-Type": "application/json" },
 body: JSON.stringify(body),
 });
 const data = await r.json();
 if (!r.ok || data.error) { toast.error(data.error || "Something went wrong."); return; }

 if (mode === "signup") {
 toast.success("Account created! Please check your email to confirm, then log in.");
 setMode("login");
 } else {
 setSession(data);
 toast.success("Welcome to DevFlow!");
 onLogin(data.user);
 }
 } catch (e) {
 toast.error("Could not connect to server.");
 } finally {
 setLoading(false);
 }
 };

 return (
 <div className="auth-screen">
 <div className="auth-card">
 <h1 className="auth-logo">DevFlow</h1>
 <p className="auth-tagline">AI-powered developer workspace</p>

 {onBackToLanding && (
 <button
 type="button"
 onClick={onBackToLanding}
 style={{
 background: "transparent",
 color: "#2563eb",
 border: "none",
 padding: "0",
 marginBottom: "18px",
 fontWeight: "700",
 cursor: "pointer"
 }}
 >
 Back to landing page
 </button>
 )}

 <div className="auth-tabs">
 <button className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>Demo Access</button>
 <button className={mode === "signup" ? "active" : ""} onClick={() => setMode("signup")}>Sign Up</button>
 </div>

 {mode === "signup" && (
 <input className="auth-input" placeholder="Full Name" value={fullName} onChange={e => setName(e.target.value)} />
 )}
 <input className="auth-input" placeholder="Email" type="email" value={email} onChange={e => setEmail(e.target.value)} />
 <input className="auth-input" placeholder="Password" type="password" value={password} onChange={e => setPass(e.target.value)}
 onKeyDown={e => e.key === "Enter" && handle()} />

 <button className="auth-btn" onClick={handle} disabled={loading}>
 {loading ? "Please wait..." : mode === "login" ? "Demo Access" : "Open Demo"}
 </button>
 </div>
 </div>
 );
}

// 
// WORKSPACE SELECTOR
// 
function WorkspaceSelector({ user, onSelect, onLogout, onAuthExpired }) {
 const [workspaces, setWorkspaces] = useState([]);
 const [newName, setNewName] = useState("");
 const [loading, setLoading] = useState(true);
 const [creating, setCreating] = useState(false);
 const didLoad = useRef(false);

 useEffect(() => {
 if (didLoad.current) return;
 didLoad.current = true;
 loadWorkspaces();
 // eslint-disable-next-line react-hooks/exhaustive-deps
 }, []);

 const loadWorkspaces = async () => {
 setLoading(true);
 try {
 const r = await authFetch("/workspaces", { method: "GET" }, onAuthExpired);
 const d = await safeJson(r);

 if (r.status === 401) return;
 if (!r.ok || d.error) {
 toast.error(d.error || "Could not load workspaces.");
 setWorkspaces([]);
 return;
 }
 setWorkspaces(d.workspaces || []);
 } catch {
 toast.error("Could not connect to server.");
 setWorkspaces([]);
 } finally {
 setLoading(false);
 }
 };

 const createWorkspace = async () => {
 if (!newName.trim()) { toast.error("Enter a workspace name."); return; }
 setCreating(true);
 try {
 const r = await authFetch("/workspaces", {
 method: "POST",
 body: JSON.stringify({ name: newName.trim() }),
 }, onAuthExpired);
 const d = await safeJson(r);

 if (r.status === 401) return;
 if (r.status === 403 && d.limitReached) { toast.error(d.error || "Free plan workspace limit reached."); return; }
 if (!r.ok || d.error) { toast.error(d.error || "Failed to create workspace."); return; }

 toast.success("Workspace created!");
 setWorkspaces(prev => [...prev, d.workspace]);
 setNewName("");
 onSelect(d.workspace);
 } catch {
 toast.error("Could not connect to server.");
 } finally {
 setCreating(false);
 }
 };

 return (
 <div className="auth-screen">
 <div className="auth-card workspace-card">
 <div className="ws-header">
 <h2>Your Workspaces</h2>
 <button className="logout-btn" onClick={onLogout}>Log Out</button>
 </div>
 <p className="auth-tagline">Logged in as <strong>{user?.email}</strong></p>

 {loading ? <p style={{textAlign:"center",color:"#64748b"}}>Loading workspaces...</p> : (
 <>
 {workspaces.length === 0 && <p style={{color:"#64748b",textAlign:"center"}}>No workspaces yet. Create one below.</p>}
 <div className="ws-list">
 {workspaces.map(ws => (
 <button key={ws.id} className="ws-item" onClick={() => onSelect(ws)}>
 <span className="ws-name">{ws.name}</span>
 <span className="ws-role">{ws.role || "member"}</span>
 </button>
 ))}
 </div>

 <div className="ws-create">
 <input className="auth-input" placeholder="New workspace name (e.g. My Team)" value={newName} onChange={e => setNewName(e.target.value)} onKeyDown={e => e.key === "Enter" && createWorkspace()} />
 <button className="auth-btn" onClick={createWorkspace} disabled={creating}>
 {creating ? "Creating..." : "+ Create Workspace"}
 </button>
 </div>
 </>
 )}
 </div>
 </div>
 );
}



function DevFlowCodingAssistant() {
 const [open, setOpen] = useState(false);
 const [input, setInput] = useState("");
 const [loading, setLoading] = useState(false);
 const [messages, setMessages] = useState([
  {
   role: "assistant",
   content: "Hi, I’m DevFlow Coding Assistant. Ask me about coding, debugging, web apps, mobile apps, software architecture, APIs, databases, GitHub, or deployment."
  }
 ]);
 const bodyRef = useRef(null);

 useEffect(() => {
  if (bodyRef.current) {
   bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }
 }, [messages, open]);

 const sendAssistantMessage = async (event) => {
  event.preventDefault();
  const question = input.trim();
  if (!question || loading) return;

  const nextMessages = [...messages, { role: "user", content: question }];
  setMessages(nextMessages);
  setInput("");
  setLoading(true);

  try {
   const response = await fetch(`${API_BASE_URL}/assistant-chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
     message: question,
     history: messages.slice(-8),
    }),
   });

   const data = await safeJson(response);
   if (!response.ok || data.error) {
    throw new Error(data.error || "Assistant failed to reply.");
   }

   setMessages(prev => [
    ...prev,
    { role: "assistant", content: cleanDevFlowOutput(data.reply || "I can help with coding questions. Please share more detail.") }
   ]);
  } catch (error) {
   setMessages(prev => [
    ...prev,
    { role: "assistant", content: error.message || "Assistant is temporarily unavailable. Please try again." }
   ]);
  } finally {
   setLoading(false);
  }
 };

 const clearAssistantChat = () => {
  setMessages([
   {
    role: "assistant",
    content: "Chat cleared. Ask me any coding, debugging, web app, mobile app, software, API, database, GitHub, or deployment question."
   }
  ]);
 };

 return (
  <>
   <button
    type="button"
    className={`assistant-fab ${open ? "assistant-fab-open" : ""}`}
    onClick={() => setOpen(!open)}
    aria-label="Open DevFlow Coding Assistant"
   >
    {open ? "×" : "AI"}
   </button>

   {open && (
    <div className="assistant-panel">
     <div className="assistant-header">
      <div>
       <strong>DevFlow Coding Assistant</strong>
       <span>Programming, web apps, mobile apps, software and debugging only</span>
      </div>
      <button type="button" className="assistant-close" onClick={() => setOpen(false)}>×</button>
     </div>

     <div className="assistant-body" ref={bodyRef}>
      {messages.map((message, index) => (
       <div key={`${message.role}-${index}`} className={`assistant-message assistant-message-${message.role}`}>
        <div className="assistant-message-label">{message.role === "user" ? "You" : "DevFlow AI"}</div>
        <div className="assistant-message-content">{message.content}</div>
       </div>
      ))}
      {loading && (
       <div className="assistant-message assistant-message-assistant">
        <div className="assistant-message-label">DevFlow AI</div>
        <div className="assistant-message-content assistant-typing">Thinking...</div>
       </div>
      )}
     </div>

     <form className="assistant-form" onSubmit={sendAssistantMessage}>
      <textarea
       value={input}
       onChange={(event) => setInput(event.target.value)}
       placeholder="Ask a coding question, paste an error, or describe a web/mobile/software problem..."
       rows={3}
       onKeyDown={(event) => {
        if (event.key === "Enter" && !event.shiftKey) {
         event.preventDefault();
         sendAssistantMessage(event);
        }
       }}
      />
      <div className="assistant-actions">
       <button type="button" className="assistant-clear" onClick={clearAssistantChat}>Clear</button>
       <button type="submit" disabled={loading || !input.trim()}>{loading ? "Sending..." : "Send"}</button>
      </div>
     </form>
    </div>
   )}
  </>
 );
}


// 
// MAIN APP
// 
function MainApp({ user, workspace, onSwitchWorkspace, onLogout, onAuthExpired }) {
 const demoMode = Boolean(user?.demo || workspace?.demo);
 const [code, setCode] = useState("");
 const [doc, setDoc] = useState("");
 const [loading, setLoading] = useState(false);
 const [fileName, setFileName] = useState("");
 const [detectedLanguage, setLang] = useState("");
 const [isDragging, setIsDragging] = useState(false);
 const [notification, setNotification] = useState("");
 const [errorMessage, setErrorMessage] = useState("");
 const [uploadedCount, setUploadedCount] = useState(0);
 const [aiEnabled, setAiEnabled] = useState(false);
 const [documentationMode, setDocumentationMode] = useState("");
 const [selectedFileCount, setSelCount] = useState(0);
 const [darkMode, setDarkMode] = useState(false);
 const [bugLog, setBugLog] = useState("");
 const [bugAnalysis, setBugAnalysis] = useState("");
 const [bugLoading, setBugLoading] = useState(false);
 const [healthReport, setHealthReport] = useState("");
 const [requirementsText, setReqText] = useState("");
 const [taskPlan, setTaskPlan] = useState("");
 const [taskLoading, setTaskLoading] = useState(false);
 const [activeModule, setActiveModule] = useState("docs");
 const [savedDocs, setSavedDocs] = useState([]);
 const [docsLoading, setDocsLoading] = useState(false);
 const [selectedDoc, setSelectedDoc] = useState(null);
 const [openingDocId, setOpeningDocId] = useState(null);
 const [saving, setSaving] = useState(false);
 const [repoUrl, setRepoUrl] = useState("");
 const [githubToken, setGithubToken] = useState("");
 const [repoLoading, setRepoLoading] = useState(false);
 const [repoDoc, setRepoDoc] = useState("");
 const [repoName, setRepoName] = useState("");
 const [inviteEmail, setInviteEmail] = useState("");
 const [inviting, setInviting] = useState(false);
 const [usageInfo, setUsageInfo] = useState(null);
 const [usageLoading, setUsageLoading] = useState(false);
 const [upgradePrompt, setUpgradePrompt] = useState(null);
 const [billingLoading, setBillingLoading] = useState(false);

 const selectedSummary = useMemo(() => {
 if (!uploadedCount && !fileName) return "No files selected yet.";
 if (selectedFileCount === 1) return `1 file selected: ${fileName}`;
 return `${selectedFileCount} files selected.`;
 }, [uploadedCount, selectedFileCount, fileName]);

 const showNotification = msg => { setNotification(msg); setTimeout(() => setNotification(""), 2500); };
 const showError = msg => { setErrorMessage(msg); setTimeout(() => setErrorMessage(""), 4000); };

 const loadUsage = async () => {
 if (demoMode) return;
 setUsageLoading(true);
 try {
 const r = await authFetch("/billing/usage", { method: "GET" }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (!r.ok || d.error) return;
 setUsageInfo(d.usage);
 } catch {
 // Keep the workspace usable even if usage panel fails temporarily.
 } finally {
 setUsageLoading(false);
 }
 };

 const applyUsageFromResponse = (data) => {
 if (data?.usage) setUsageInfo(data.usage);
 };

 const handleLimitReached = (data) => {
 if (data?.usage) setUsageInfo(data.usage);
 setUpgradePrompt(data || { error: "Free plan limit reached. Upgrade to continue." });
 toast.error(data?.error || "Free plan limit reached. Upgrade to continue.");
 };

 const changeDemoPlan = async (plan) => {
 try {
 const r = await authFetch("/billing/demo-upgrade", {
 method: "POST",
 body: JSON.stringify({ plan }),
 }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (!r.ok || d.error) { toast.error(d.error || "Could not change plan."); return; }
 if (d.usage) setUsageInfo(d.usage);
 toast.success(d.message || "Plan updated.");
 setUpgradePrompt(null);
 } catch {
 toast.error("Could not connect to billing service.");
 }
 };

 const startStripeCheckout = async (plan) => {
 if (!plan || plan === "free") return;
 setBillingLoading(true);
 try {
 const r = await authFetch("/billing/create-checkout-session", {
 method: "POST",
 body: JSON.stringify({ plan }),
 }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (!r.ok || d.error) {
 toast.error(d.error || "Could not start Stripe checkout.");
 return;
 }
 if (!d.checkout_url) {
 toast.error("Stripe checkout URL was not returned.");
 return;
 }
 window.location.href = d.checkout_url;
 } catch {
 toast.error("Could not connect to Stripe checkout service.");
 } finally {
 setBillingLoading(false);
 }
 };

 const openCustomerPortal = async () => {
 setBillingLoading(true);
 try {
 const r = await authFetch("/billing/create-portal-session", {
 method: "POST",
 }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (!r.ok || d.error) {
 toast.error(d.error || "Could not open Stripe customer portal.");
 return;
 }
 if (d.portal_url) window.location.href = d.portal_url;
 } catch {
 toast.error("Could not connect to billing portal.");
 } finally {
 setBillingLoading(false);
 }
 };

 const refreshStripeSubscription = async () => {
 setBillingLoading(true);
 try {
 const r = await authFetch("/billing/refresh-subscription", {
 method: "POST",
 }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (!r.ok || d.error) {
 toast.error(d.error || "Could not sync Stripe subscription.");
 return;
 }
 if (d.usage) setUsageInfo(d.usage);
 toast.success(d.message || "Stripe subscription synced.");
 } catch {
 toast.error("Could not connect to Stripe subscription service.");
 } finally {
 setBillingLoading(false);
 }
 };

 const syncStripeSessionFromUrl = async () => {
 if (demoMode) return;
 const params = new URLSearchParams(window.location.search);
 const sessionId = params.get("session_id");
 const stripeSuccess = params.get("stripe_success");
 const stripeCancelled = params.get("stripe_cancelled");

 if (stripeCancelled) {
 toast.error("Stripe checkout was cancelled.");
 window.history.replaceState({}, document.title, window.location.pathname);
 return;
 }

 if (!stripeSuccess || !sessionId) return;

 setBillingLoading(true);
 try {
 const r = await authFetch("/billing/sync-session", {
 method: "POST",
 body: JSON.stringify({ session_id: sessionId }),
 }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (!r.ok || d.error) {
 toast.error(d.error || "Could not activate subscription.");
 return;
 }
 if (d.usage) setUsageInfo(d.usage);
 toast.success(d.message || "Subscription activated.");
 setActiveModule("billing");
 } catch {
 toast.error("Could not verify Stripe checkout.");
 } finally {
 window.history.replaceState({}, document.title, window.location.pathname);
 setBillingLoading(false);
 }
 };

 const usageRows = useMemo(() => {
 const f = usageInfo?.features || {};
 return [
 ["Docs + GitHub", f.documentation_generations],
 ["Bug Analyzer", f.bug_analyzer],
 ["Smart Health", f.project_health],
 ["Task Generator", f.task_generator],
 ["Workspaces", usageInfo?.workspace],
 ].filter(([, value]) => Boolean(value));
 }, [usageInfo]);

 const usageText = (item) => {
 if (!item) return "";
 if (item.unlimited || item.limit === null) return `${item.used} / Unlimited`;
 return `${item.used} / ${item.limit}`;
 };

 // Load saved docs when switching to history tab
 useEffect(() => {
 if (activeModule === "history") {
 setSelectedDoc(null);
 loadDocs();
 }
 }, [activeModule]);

 useEffect(() => {
 loadUsage();
 syncStripeSessionFromUrl();
 // eslint-disable-next-line react-hooks/exhaustive-deps
 }, []);

 const loadDocs = async () => {
 setDocsLoading(true);
 try {
 const r = await authFetch(`/workspaces/${workspace.id}/documents`, { method: "GET" }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (!r.ok || d.error) { toast.error(d.error || "Could not load documents."); return; }
 setSavedDocs(d.documents || []);
 } catch { toast.error("Could not load documents."); }
 finally { setDocsLoading(false); }
 };

 const openSavedDoc = async (docId) => {
    setOpeningDocId(docId);
    try {
      const r = await authFetch(`/documents/${docId}`, { method: "GET" }, onAuthExpired);
      const d = await safeJson(r);
      if (r.status === 401) return;
      if (!r.ok || d.error) {
        toast.error(d.error || "Could not open document.");
        return;
      }
      setSelectedDoc(d.document);
    } catch {
      toast.error("Could not open document.");
    } finally {
      setOpeningDocId(null);
    }
  }; const saveWorkspaceDocument = async ({ title, language, content, file_count, successMessage }) => {
 if (!content) { toast.error("Nothing to save yet."); return; }
 if (demoMode) { toast.success("Demo mode: use Copy, Markdown, or PDF export instead of workspace saving."); return; }
 setSaving(true);
 try {
 const r = await authFetch(`/workspaces/${workspace.id}/documents`, {
 method: "POST",
 body: JSON.stringify({
 title: title || "Untitled DevFlow Document",
 language: language || "Unknown",
 content,
 file_count: file_count || 1,
 }),
 }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (!r.ok || d.error) { toast.error(d.error || "Failed to save."); return; }
 toast.success(successMessage || "Saved to workspace!");
 if (activeModule === "history") loadDocs();
 } finally {
 setSaving(false);
 }
 };

 const saveDoc = async () => {
 if (!doc) { toast.error("Generate documentation first."); return; }
 const title = fileName ? `Docs: ${fileName.split(",")[0]}` : "Untitled Documentation";
 await saveWorkspaceDocument({
 title,
 language: detectedLanguage || "Documentation",
 content: doc,
 file_count: uploadedCount || 1,
 successMessage: "Documentation saved to workspace!",
 });
 };

 const saveHealthReport = async () => {
 if (!healthReport) { toast.error("Generate a smart health review first."); return; }
 const title = fileName ? `Health Report: ${fileName.split(",")[0]}` : "Smart Health Report";
 await saveWorkspaceDocument({
 title,
 language: "Smart Health",
 content: healthReport,
 file_count: uploadedCount || 1,
 successMessage: "Smart health review saved to workspace!",
 });
 };

 const deleteDoc = async (docId) => {
 const r = await authFetch(`/documents/${docId}`, { method: "DELETE" }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (!r.ok || d.error) { toast.error(d.error || "Could not delete document."); return; }
 setSavedDocs(prev => prev.filter(d => d.id !== docId));
 if (selectedDoc?.id === docId) setSelectedDoc(null);
 toast.success("Deleted.");
 };

 const inviteMember = async () => {
 if (!inviteEmail.trim()) { toast.error("Enter an email address."); return; }
 setInviting(true);
 try {
 const r = await authFetch(`/workspaces/${workspace.id}/members`, {
 method: "POST",
 body: JSON.stringify({ email: inviteEmail }),
 }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (!r.ok || d.error) { toast.error(d.error || "Could not invite member."); return; }
 toast.success(`${inviteEmail} added to workspace!`);
 setInviteEmail("");
 } finally { setInviting(false); }
 };

 const shouldIgnoreFile = filePath => {
 const p = String(filePath || "").toLowerCase().replace(/\\/g, "/");
 const base = p.split("/").pop() || "";
 const ignoredParts = [
 "node_modules","venv",".venv","__pycache__",".git","dist","build",".next","coverage",
 "images","assets","media",".cache",".pytest_cache",".mypy_cache"
 ];
 const ignoredFiles = [
 "package-lock.json","yarn.lock","pnpm-lock.yaml",".env",".env.local",".env.production",
 ".gitignore",".ds_store","thumbs.db"
 ];
 const ignoredExts = [
 ".png",".jpg",".jpeg",".gif",".svg",".ico",".pdf",".zip",".rar",".7z",".exe",".dll",
 ".mp4",".mp3",".wav",".woff",".woff2",".ttf",".otf",".map",".log",".pyc"
 ];
 const generatedOrBackup =
 base.includes("_backup") ||
 base.includes(".backup") ||
 base.includes("hotfix") ||
 base.startsWith("fix_") ||
 base.startsWith("readme_fix") ||
 base.endsWith(".bak") ||
 base.endsWith(".old");

 if (ignoredParts.some(x => p.includes(x))) return true;
 if (ignoredFiles.some(x => base === x || p.endsWith("/" + x))) return true;
 if (ignoredExts.some(x => p.endsWith(x))) return true;
 if (generatedOrBackup) return true;
 return false;
 };

 const readFileAsText = file => new Promise((resolve, reject) => {
 const reader = new FileReader();
 reader.onload = e => resolve({ name: file.webkitRelativePath || file.name, content: e.target.result || "" });
 reader.onerror = () => reject(new Error(`Failed to read ${file.name}`));
 reader.readAsText(file);
 });

 const handleFileUpload = async event => {
 setErrorMessage("");
 const selectedFiles = Array.from(event.target.files || []);
 const supportedFiles = selectedFiles.filter(f => {
 const fp = f.webkitRelativePath || f.name;
 return !shouldIgnoreFile(fp) && f.size <= MAX_FILE_SIZE_MB * 1024 * 1024;
 });
 setSelCount(supportedFiles.length);

 if (supportedFiles.length === 0) {
 showError("No supported code files found. DevFlow skips backups, images, PDFs, virtual environments, node_modules, and generated files.");
 return;
 }

 const filesToRead = supportedFiles.slice(0, MAX_TOTAL_FILES);

 try {
 const results = await Promise.all(filesToRead.map(readFileAsText));
 const smartPackage = buildSmartUploadPackage(results, supportedFiles.length);

 setCode(smartPackage.code);
 setDoc("");
 setLang("");
 setHealthReport("");
 setUploadedCount(results.length);
 setDocumentationMode(formatDocumentationMode(smartPackage.mode));
 setFileName(results.map(f => f.name).join(", "));

 const skippedByLimit = supportedFiles.length > MAX_TOTAL_FILES ? ` Loaded first ${MAX_TOTAL_FILES} supported files.` : "";
 const compactedNote = smartPackage.omitted > 0 ? ` ${smartPackage.omitted} low-priority files were summarized or skipped safely.` : "";
 showNotification(`${formatDocumentationMode(smartPackage.mode)} loaded in smart mode.${skippedByLimit}${compactedNote}`);
 } catch (e) {
 showError(e.message || "Failed to read files.");
 }
 finally {
 if (event.target) event.target.value = "";
 }
 };

 const handleDrop = async e => {
 e.preventDefault(); e.stopPropagation(); setIsDragging(false);
 try { await handleFileUpload({ target: { files: e.dataTransfer.files } }); }
 catch { toast.error("Failed to read dropped files."); }
 };

 const cleanDoc = (text) => {
    let value = String(text || "");

    value = value
      .replace(/\r\n/g, "\n")
      .replace(/```(?:python|javascript|js|jsx|ts|tsx|json|bash|shell|html|css|markdown|md)?/gi, "")
      .replace(/```/g, "")
      .replace(/^\s*---\s*FILE:\s*(.*?)\s*---\s*$/gim, "\n# Source File: $1\n")
      .replace(/^\s*FILE:\s*(.*?)\s*$/gim, "# Source File: $1")
      .replace(/^\s*LANGUAGE:\s*(.*?)\s*$/gim, "Language: $1")
      .replace(/^\s*-\s*Total Lines:\s*/gim, "Total Lines: ")
      .replace(/^\s*-\s*Non-empty Lines:\s*/gim, "Non-empty Lines: ")
      .replace(/^\s*Large Python File\s*$/gim, "Large File Summary")
      .replace(/^\s*Functions:\s*$/gim, "Detected Functions")
      .replace(/^\s*[-=]{6,}\s*$/gm, "")
      .replace(/AI Notice:\s*Request too large[\s\S]*?(?=\n#|\n[A-Z][A-Za-z ]+\n|$)/gi, "")
      .replace(/The model request is too large[\s\S]*?(?=\n#|\n[A-Z][A-Za-z ]+\n|$)/gi, "")
      .replace(/Request too large[\s\S]*?(?=\n#|\n[A-Z][A-Za-z ]+\n|$)/gi, "")
      .replace(/org_[a-z0-9_]+/gi, "[provider-id-hidden]")
      .replace(/https:\/\/console\.groq\.com\/settings\/billing/gi, "")
      .replace(/\*\*(.*?)\*\*/g, "$1")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();

    return value;
  }; const handleGenerateDoc = async () => {
 if (!code.trim()) { showError("Please paste code or upload a project first."); return; }
 setLoading(true); setDoc(""); setLang(""); setErrorMessage("");
 try {
 const requestCode = prepareCodeForRequest(code, fileName);
 const r = await authFetch(`/generate-doc`, {
 method:"POST",
 body:JSON.stringify({
 code: requestCode,
 fileName,
 smartMode: requestCode.length !== code.length || documentationMode === "Full Project"
 })
 }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (r.status === 403 && d.limitReached) { handleLimitReached(d); return; }
 if (!r.ok || d.error) {
 const rawError = String(d.error || "");
 if (/too large|request entity|payload/i.test(rawError)) {
 throw new Error("This project was too large for one request. DevFlow compressed the upload, but the server still rejected it. Try uploading only app.py, frontend/src/App.js, frontend/src/App.css, package.json, requirements.txt, Procfile, and railway.json.");
 }
 throw new Error(rawError || "Something went wrong.");
 }
 setDoc(d.doc||"No documentation returned.");
 setLang(d.language||"");
 setAiEnabled(Boolean(d.aiEnabled));
 setDocumentationMode(formatDocumentationMode(d.inputType || d.documentationMode || documentationMode));
 applyUsageFromResponse(d);
 showNotification("Professional documentation generated!");
 } catch(e) { showError(e.message||"Failed to connect to backend."); }
 finally { setLoading(false); }
 };

 const handleProjectHealth = async () => {
 if (!code.trim()) { showError("Please upload a project first."); return; }
 setLoading(true);
 try {
 const r = await authFetch(`/project-health`, { method:"POST", body:JSON.stringify({code}) }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (r.status === 403 && d.limitReached) { handleLimitReached(d); return; }
 if (!r.ok || d.error) throw new Error(d.error||"Failed to generate health report.");
 const rp = d.report;
 setHealthReport(formatHealthReport(rp));
 setActiveModule("health");
 applyUsageFromResponse(d);
 showNotification("Health report generated.");
 } catch(e) { showError(e.message); }
 finally { setLoading(false); }
 };

 const handleAnalyzeBug = async () => {
 if (!bugLog.trim()) { showError("Please paste an error log first."); return; }
 setBugLoading(true); setBugAnalysis("");
 try {
 const r = await authFetch(`/analyze-bug`, { method:"POST", body:JSON.stringify({error_log:bugLog}) }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (r.status === 403 && d.limitReached) { handleLimitReached(d); return; }
 if (!r.ok) throw new Error(d.error||"Failed to analyze bug.");
 setBugAnalysis(cleanDevFlowOutput(d.analysis || "No analysis returned.")); applyUsageFromResponse(d); showNotification("Bug analyzed!");
 } catch(e) { showError(e.message); }
 finally { setBugLoading(false); }
 };

 const handleGenerateTasks = async () => {
 if (!requirementsText.trim()) { showError("Please paste requirements first."); return; }
 setTaskLoading(true); setTaskPlan("");
 try {
 const r = await authFetch(`/generate-tasks`, { method:"POST", body:JSON.stringify({requirements:requirementsText}) }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (r.status === 403 && d.limitReached) { handleLimitReached(d); return; }
 if (!r.ok || d.error) throw new Error(d.error||"Failed to generate tasks.");
 applyUsageFromResponse(d);
 setTaskPlan(formatGeneratedTasks(d.tasks || []));
 showNotification("Tasks generated!");
 } catch(e) { showError(e.message); }
 finally { setTaskLoading(false); }
 };

 const downloadTextFile = (content, name, type) => {
 const blob = new Blob([content],{type}); const url = URL.createObjectURL(blob);
 const a = document.createElement("a"); a.href=url; a.download=name; a.click(); URL.revokeObjectURL(url);
 };

 const handleExportPDF = () => {
    if (!doc) {
      toast.error("Generate documentation first.");
      return;
    }

    const safeFileName = `devflow-documentation-${new Date().toISOString().slice(0, 10)}.pdf`;
    const safeTitle = fileName
      ? `${fileName.replace(/[-_]+/g, " ")} Documentation`
      : "DevFlow Documentation";

    exportContentAsPDF(safeTitle, doc, safeFileName);
  }; const exportContentAsPDF = (title, content, fileNameToSave) => {
    if (!content) {
      toast.error("Nothing to export yet.");
      return;
    }

    try {
      toast.loading("Preparing professional PDF...", { id: "pdf-export" });

      const pdf = new jsPDF("p", "mm", "a4");
      const pageWidth = pdf.internal.pageSize.getWidth();
      const pageHeight = pdf.internal.pageSize.getHeight();
      const margin = 16;
      const contentWidth = pageWidth - margin * 2;
      const footerY = pageHeight - 12;
      let y = 18;
      let page = 1;

      const palette = {
        ink: [15, 23, 42],
        slate: [51, 65, 85],
        muted: [100, 116, 139],
        line: [226, 232, 240],
        soft: [248, 250, 252],
        softBlue: [239, 246, 255],
        blue: [37, 99, 235],
        blueDark: [30, 64, 175],
        white: [255, 255, 255],
        green: [5, 150, 105],
      };

      const safeTitle = String(title || "DevFlow Documentation")
        .replace(/[-_]+/g, " ")
        .replace(/\s+/g, " ")
        .trim();

      const stripMarkdown = (value = "") => String(value)
        .replace(/\r\n/g, "\n")
        .replace(/\r/g, "\n")
        .replace(/```[a-zA-Z0-9_-]*\n?/g, "")
        .replace(/```/g, "")
        .replace(/\*\*(.*?)\*\*/g, "$1")
        .replace(/__(.*?)__/g, "$1")
        .replace(/`([^`]+)`/g, "$1")
        .replace(/\t/g, "  ")
        .replace(/\n{3,}/g, "\n\n")
        .trim();

      const prepared = stripMarkdown(cleanDevFlowOutput(cleanDoc(content)));

      const sanitizeText = (value = "") => String(value)
        .replace(/[•]/g, "-")
        .replace(/[→]/g, "->")
        .replace(/[“”]/g, '"')
        .replace(/[‘’]/g, "'")
        .replace(/[—–]/g, "-")
        .replace(/[✓✔]/g, "check")
        .replace(/[✕✖]/g, "x")
        .replace(/[\u{1F300}-\u{1FAFF}]/gu, "")
        .replace(/\s+/g, " ")
        .trim();

      const addFooter = () => {
        pdf.setDrawColor(...palette.line);
        pdf.setLineWidth(0.2);
        pdf.line(margin, footerY - 4, pageWidth - margin, footerY - 4);
        pdf.setFont("helvetica", "normal");
        pdf.setFontSize(7.6);
        pdf.setTextColor(...palette.muted);
        pdf.text("Generated by DevFlow", margin, footerY);
        pdf.text(`Page ${page}`, pageWidth - margin - 14, footerY);
      };

      const addPage = () => {
        addFooter();
        pdf.addPage();
        page += 1;
        y = 20;
      };

      const ensureSpace = (needed = 12) => {
        if (y + needed > footerY - 4) addPage();
      };

      const writeWrapped = (text, options = {}) => {
        const {
          size = 10,
          style = "normal",
          color = palette.ink,
          lineGap = 5,
          before = 0,
          after = 2,
          indent = 0,
          maxWidth = contentWidth - indent,
        } = options;

        const cleaned = sanitizeText(text);
        if (!cleaned) return;
        if (before) y += before;
        pdf.setFont("helvetica", style);
        pdf.setFontSize(size);
        pdf.setTextColor(...color);
        const wrapped = pdf.splitTextToSize(cleaned, maxWidth);
        wrapped.forEach((line) => {
          ensureSpace(lineGap + 2);
          pdf.text(line, margin + indent, y);
          y += lineGap;
        });
        if (after) y += after;
      };

      const writeSection = (heading) => {
        const label = sanitizeText(heading.replace(/^#{1,6}\s*/, "").replace(/:$/, ""));
        if (!label) return;
        ensureSpace(18);
        y += 3;
        pdf.setFillColor(...palette.softBlue);
        pdf.roundedRect(margin, y - 6, contentWidth, 12, 2.5, 2.5, "F");
        pdf.setFont("helvetica", "bold");
        pdf.setFontSize(12.2);
        pdf.setTextColor(...palette.blueDark);
        pdf.text(label.slice(0, 90), margin + 4, y + 1.8);
        y += 10;
      };

      const writeSubheading = (heading) => {
        const label = sanitizeText(heading.replace(/^#{1,6}\s*/, "").replace(/:$/, ""));
        if (!label) return;
        writeWrapped(label, {
          size: 10.8,
          style: "bold",
          color: palette.ink,
          lineGap: 5.2,
          before: 1,
          after: 1.2,
        });
      };

      const writeMeta = (key, value) => {
        ensureSpace(9);
        const label = sanitizeText(key).slice(0, 34);
        const val = sanitizeText(value).slice(0, 130);
        pdf.setFont("helvetica", "bold");
        pdf.setFontSize(8.8);
        pdf.setTextColor(...palette.slate);
        pdf.text(label, margin, y);
        pdf.setFont("helvetica", "normal");
        pdf.setTextColor(...palette.ink);
        const valLines = pdf.splitTextToSize(val || "-", contentWidth - 42);
        pdf.text(valLines, margin + 42, y);
        y += Math.max(5.2, valLines.length * 4.8);
      };

      const addCover = () => {
        pdf.setFillColor(...palette.ink);
        pdf.roundedRect(margin, 14, contentWidth, 35, 4, 4, "F");
        pdf.setFillColor(...palette.blue);
        pdf.rect(margin, 46, contentWidth, 3, "F");

        pdf.setFont("helvetica", "bold");
        pdf.setFontSize(22);
        pdf.setTextColor(...palette.white);
        pdf.text("DevFlow", margin + 8, 28);

        pdf.setFont("helvetica", "normal");
        pdf.setFontSize(9.5);
        pdf.setTextColor(203, 213, 225);
        pdf.text("AI-powered developer documentation report", margin + 8, 39);

        y = 62;
        pdf.setFont("helvetica", "bold");
        pdf.setFontSize(17);
        pdf.setTextColor(...palette.ink);
        const titleLines = pdf.splitTextToSize(safeTitle, contentWidth);
        pdf.text(titleLines, margin, y);
        y += titleLines.length * 7 + 3;

        pdf.setFont("helvetica", "normal");
        pdf.setFontSize(8.8);
        pdf.setTextColor(...palette.muted);
        const meta = [
          `Exported ${new Date().toLocaleDateString()}`,
          detectedLanguage ? `Language: ${detectedLanguage}` : "",
          uploadedCount ? `Files: ${uploadedCount}` : "",
          documentationMode ? `Mode: ${documentationMode}` : "",
        ].filter(Boolean).join("   |   ");
        pdf.text(meta, margin, y);
        y += 10;

        pdf.setFillColor(...palette.soft);
        pdf.roundedRect(margin, y, contentWidth, 17, 3, 3, "F");
        pdf.setFont("helvetica", "bold");
        pdf.setFontSize(9);
        pdf.setTextColor(...palette.slate);
        pdf.text("Report type", margin + 5, y + 7);
        pdf.setFont("helvetica", "normal");
        pdf.setTextColor(...palette.ink);
        pdf.text("Structured developer documentation", margin + 43, y + 7);
        pdf.setFont("helvetica", "bold");
        pdf.setTextColor(...palette.slate);
        pdf.text("Prepared for", margin + 5, y + 13);
        pdf.setFont("helvetica", "normal");
        pdf.setTextColor(...palette.ink);
        pdf.text("Code review, onboarding, handover, and planning", margin + 43, y + 13);
        y += 27;
      };

      addCover();

      const lines = prepared.split("\n");
      lines.forEach((rawLine) => {
        const raw = String(rawLine || "").trimEnd();
        const trimmed = raw.trim();

        if (!trimmed) {
          y += 2.6;
          return;
        }

        if (/^[-=]{5,}$/.test(trimmed)) {
          ensureSpace(7);
          pdf.setDrawColor(...palette.line);
          pdf.line(margin, y, pageWidth - margin, y);
          y += 5;
          return;
        }

        const sourceFileMatch = trimmed.match(/^#?\s*Source File:\s*(.+)$/i) || trimmed.match(/^FILE:\s*(.+)$/i);
        if (sourceFileMatch) {
          ensureSpace(16);
          pdf.setFillColor(...palette.soft);
          pdf.roundedRect(margin, y - 2, contentWidth, 12, 2.5, 2.5, "F");
          pdf.setFont("helvetica", "bold");
          pdf.setFontSize(9.5);
          pdf.setTextColor(...palette.slate);
          pdf.text("Source file", margin + 4, y + 5.5);
          pdf.setFont("helvetica", "normal");
          pdf.setTextColor(...palette.ink);
          pdf.text(sanitizeText(sourceFileMatch[1]).slice(0, 96), margin + 36, y + 5.5);
          y += 15;
          return;
        }

        if (/^#{1,2}\s+/.test(trimmed)) {
          writeSection(trimmed);
          return;
        }

        if (/^#{3,6}\s+/.test(trimmed)) {
          writeSubheading(trimmed);
          return;
        }

        if (isDevFlowSectionHeading(trimmed)) {
          writeSection(trimmed);
          return;
        }

        const metaMatch = trimmed.match(/^([A-Za-z][A-Za-z0-9 /&()+_.-]{1,34}):\s+(.+)$/);
        if (metaMatch && trimmed.length <= 170) {
          writeMeta(metaMatch[1], metaMatch[2]);
          return;
        }

        if (/^\|\s*[-:]+/.test(trimmed)) return;

        if (/^\|/.test(trimmed)) {
          const cells = trimmed.split("|").map((x) => x.trim()).filter(Boolean);
          if (cells.length >= 2) {
            writeMeta(cells[0], cells.slice(1).join(" | "));
            return;
          }
        }

        const bulletMatch = trimmed.match(/^[-*•]\s+(.+)$/);
        if (bulletMatch) {
          writeWrapped(`- ${bulletMatch[1]}`, {
            size: 9.8,
            color: palette.slate,
            lineGap: 4.8,
            indent: 4,
            after: 0.8,
          });
          return;
        }

        const numberedMatch = trimmed.match(/^\d+\.\s+(.+)$/);
        if (numberedMatch) {
          writeWrapped(trimmed, {
            size: 9.8,
            color: palette.slate,
            lineGap: 4.8,
            indent: 4,
            after: 0.8,
          });
          return;
        }

        writeWrapped(trimmed, {
          size: 10,
          color: palette.ink,
          lineGap: 5.15,
          after: 1.2,
        });
      });

      addFooter();
      const filename = (fileNameToSave || "devflow-documentation.pdf").replace(/[^a-z0-9_.-]/gi, "-");
      pdf.save(filename);
      toast.success("Professional PDF exported!", { id: "pdf-export" });
    } catch (e) {
      console.error(e);
      toast.error(e.message || "PDF export failed.", { id: "pdf-export" });
    }
  }; const saveRepoDoc = async () => {
 if (!repoDoc) { toast.error("Generate GitHub documentation first."); return; }
 setSaving(true);
 try {
 const title = repoName ? `GitHub Docs: ${repoName}` : "GitHub Repository Documentation";
 const r = await authFetch(`/workspaces/${workspace.id}/documents`, {
 method: "POST",
 body: JSON.stringify({
 title,
 language: "Repository",
 content: repoDoc,
 file_count: 1,
 }),
 }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (!r.ok || d.error) { toast.error(d.error || "Failed to save GitHub docs."); return; }
 toast.success("GitHub documentation saved to workspace!");
 if (activeModule === "history") loadDocs();
 } finally {
 setSaving(false);
 }
 };

 const handleGithubDocument = async () => {
 if (!repoUrl.trim()) { showError("Please enter a GitHub repository URL."); return; }
 setRepoLoading(true); setRepoDoc("");
 try {
 const r = await authFetch(`/github/document`, {
 method: "POST",
 body: JSON.stringify({ repo_url: repoUrl, github_token: githubToken }),
 }, onAuthExpired);
 const d = await safeJson(r);
 if (r.status === 401) return;
 if (r.status === 403 && d.limitReached) { handleLimitReached(d); return; }
 if (!r.ok || !d.success) { showError(d.error || "Failed to fetch repo."); return; }
 setRepoDoc(d.doc); setRepoName(d.repo_name); applyUsageFromResponse(d);
 showNotification(`Fast GitHub report generated for ${d.file_count} files from ${d.repo_name}!`);
 } catch(e) { showError("Could not connect to backend."); }
 finally { setRepoLoading(false); }
 };

 const handleClearAll = () => {
 setCode(""); setDoc(""); setFileName(""); setLang(""); setUploadedCount(0);
 setAiEnabled(false); setDocumentationMode(""); setErrorMessage(""); setHealthReport("");
 setBugAnalysis(""); setBugLog(""); setReqText(""); setTaskPlan(""); setSelCount(0);
 showNotification("Cleared.");
 };

 const billing = usageInfo?.billing || {};
 const currentPlan = String(usageInfo?.plan || "free").toLowerCase();
 const subscriptionStatus = billing.subscription_status || (currentPlan === "free" ? "free" : "active");
 const periodEndLabel = billing.current_period_end
 ? new Date(billing.current_period_end).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
 : "Not available";
 const billingPlanCards = [
 {
 plan: "free",
 title: "Free",
 price: "$0",
 suffix: "forever",
 badge: "Starter",
 description: "For testing DevFlow and small personal experiments.",
 items: ["5 docs or GitHub reports per month", "1 workspace", "3 bug analyses", "2 health reports", "3 task generations"]
 },
 {
 plan: "pro",
 title: "Pro",
 price: "$19",
 suffix: "per month",
 badge: "Most popular",
 description: "For active developers building and documenting real projects.",
 items: ["Unlimited documentation", "GitHub repo documentation", "3 workspaces", "Unlimited AI tools", "Priority AI responses"]
 },
 {
 plan: "team",
 title: "Team",
 price: "$49",
 suffix: "per user/month",
 badge: "For companies",
 description: "For software teams that need shared knowledge and billing control.",
 items: ["Unlimited workspaces", "Unlimited team members", "Admin dashboard roadmap", "Audit logs roadmap", "Priority support roadmap"]
 }
 ];

 return (
 <div className={`app-container ${darkMode?"dark-mode":""}`}>
 <DevFlowCodingAssistant />
 {notification && <div className="toast-notification">{notification}</div>}
 {errorMessage && <div className="toast-notification error-toast">{errorMessage}</div>}

 <header className="hero-section">
 <div className="hero-top">
 <h1>DevFlow</h1>
 <div className="hero-user-bar">
 <span className="workspace-badge"> {workspace.name}</span>
 {!demoMode && <button className="nav-small-btn" onClick={onSwitchWorkspace}>Switch</button>}
 {!demoMode && <button className="nav-small-btn danger" onClick={onLogout}>Logout</button>}
 <button className="theme-toggle" onClick={() => setDarkMode(!darkMode)}>
 {darkMode ? "Light" : "Dark"}
 </button>
 </div>
 </div>

 <div className="workspace-nav">
 {(demoMode ? ["docs","bugs","health","tasks","github"] : ["docs","bugs","health","tasks","github","history","team","billing"]).map(m => (
 <button key={m} className={activeModule===m?"nav-active":""} onClick={() => setActiveModule(m)}>
 {m === "docs" ? "Documentation" : m === "bugs" ? "Bug Analyzer" : m === "health" ? "Smart Health" : m === "tasks" ? "Task Generator" : m === "history" ? " Saved Docs" : m === "github" ? " GitHub" : m === "billing" ? " Billing" : " Team"}
 </button>
 ))}
 </div>
 <p>{demoMode ? "Public live demo - no login or signup required" : `AI-powered developer workspace - ${workspace.name}`}</p>
 </header>

 {!demoMode && upgradePrompt && (
 <div className="loading-overlay">
 <div className="loading-card" style={{maxWidth:"560px", textAlign:"left"}}>
 <h2 style={{marginTop:0}}>Upgrade Required</h2>
 <p>{upgradePrompt.error || "You reached your free plan limit."}</p>
 <div style={{background:"#f8fafc", border:"1px solid #e2e8f0", borderRadius:"14px", padding:"14px", margin:"14px 0"}}>
 <strong>Free plan limits</strong>
 <p style={{margin:"8px 0 0", color:"#64748b"}}>5 docs/GitHub reports, 3 bug analyses, 2 health reports, 3 task generations, and 1 workspace per month.</p>
 </div>
 <div style={{display:"flex", gap:"10px", flexWrap:"wrap"}}>
 <button onClick={() => { setUpgradePrompt(null); setActiveModule("billing"); }}>View Pricing</button>
 <button className="secondary-btn" onClick={() => startStripeCheckout("pro")} disabled={billingLoading}>{billingLoading ? "Opening Stripe..." : "Upgrade to Pro"}</button>
 <button className="danger-btn" onClick={() => setUpgradePrompt(null)}>Close</button>
 </div>
 </div>
 </div>
 )}

 {!demoMode && usageInfo && (
 <section style={{maxWidth:"1180px", margin:"18px auto 0", padding:"18px", background:"#ffffff", border:"1px solid #e2e8f0", borderRadius:"20px", boxShadow:"0 10px 28px rgba(15, 23, 42, 0.06)"}}>
 <div style={{display:"flex", justifyContent:"space-between", gap:"16px", alignItems:"center", flexWrap:"wrap"}}>
 <div>
 <strong style={{fontSize:"18px"}}>Plan: {formatPlanName(usageInfo?.plan)}</strong>
 <p style={{margin:"4px 0 0", color:"#64748b"}}>Usage period: {usageInfo.period} {usageLoading ? " Refreshing..." : ""}</p>
 </div>
 <div style={{display:"flex", gap:"10px", flexWrap:"wrap"}}>
 <button className="secondary-btn" onClick={loadUsage}>Refresh Usage</button>
 <button onClick={() => setActiveModule("billing")}>Upgrade</button>
 </div>
 </div>
 <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fit, minmax(160px, 1fr))", gap:"12px", marginTop:"16px"}}>
 {usageRows.map(([label, item]) => (
 <div key={label} style={{background:"#f8fafc", border:"1px solid #e2e8f0", borderRadius:"14px", padding:"12px"}}>
 <span style={{display:"block", color:"#64748b", fontSize:"13px", marginBottom:"6px"}}>{label}</span>
 <strong>{usageText(item)}</strong>
 </div>
 ))}
 </div>
 </section>
 )}

 <main className="main-grid">

 {/* DOCUMENTATION */}
 {activeModule === "docs" && (<>
 <section className="card">
 <h2>Paste or Upload Project Code</h2>
 <div className="upload-module">
 <label className="upload-label">Upload single or multiple files</label>
 <input type="file" className="file-input" onChange={handleFileUpload} accept=".py,.js,.jsx,.ts,.tsx,.html,.css,.sql,.php,.java,.cpp,.c,.cs,.kt,.swift,.dart,.txt,.md,.json" multiple />
 </div>
 <div className="upload-module">
 <label className="upload-label">Upload full project folder</label>
 <input type="file" className="file-input" onChange={handleFileUpload} webkitdirectory="true" directory="true" multiple />
 </div>
 <p className="file-name">{selectedSummary}</p>
 <div className={`drop-zone ${isDragging?"drag-active":""}`}
 onDragEnter={e=>{e.preventDefault();e.stopPropagation();setIsDragging(true)}}
 onDragOver={e=>{e.preventDefault();e.stopPropagation();setIsDragging(true)}}
 onDragLeave={e=>{e.preventDefault();e.stopPropagation();setIsDragging(false)}}
 onDrop={handleDrop}>
 <strong>Drag & drop source files here</strong>
 <span>or use the upload buttons above</span>
 </div>
 <textarea className="code-textarea" value={code} onChange={e=>{setCode(e.target.value);setDoc("");setLang("");setHealthReport("");}}
 placeholder={`Upload files or paste code, then click Generate.\n\nSupported:\n- Single code snippets\n- Full files\n- Multiple files\n- Project folders`} />
 {loading && <div className="loading-overlay"><div className="loading-card"><InfinitySpin width="220" color="#2563eb"/><h2>Analyzing Project...</h2><p>Generating documentation with AI...</p></div></div>}
 <div className="button-row">
 <button onClick={handleGenerateDoc} disabled={loading||!code.trim()}>{loading?"Generating...":"Generate"}</button>
 <button onClick={handleProjectHealth} disabled={loading||!code.trim()}>Health Review</button>
 <button className="secondary-btn" onClick={saveDoc} disabled={!doc||saving||demoMode}>{demoMode ? "Demo Save Off" : saving?"Saving...":" Save"}</button>
 <button className="secondary-btn" onClick={()=>{if(!doc){toast.error("Generate first.");return;}navigator.clipboard.writeText(cleanDoc(doc));toast.success("Copied!");}}>Copy</button>
 <button className="secondary-btn" onClick={()=>{if(!doc){toast.error("Generate first.");return;}downloadTextFile(doc,"documentation.md","text/markdown");toast.success("Markdown exported!");}}>Markdown</button>
 <button className="secondary-btn" onClick={handleExportPDF} disabled={!doc}>PDF</button>
 <button className="danger-btn" onClick={handleClearAll} disabled={loading}>Clear</button>
 </div>
 </section>
 <section className="card docs-card">
 <h2>Generated Documentation</h2>
 <div className="stats-grid">
 <div className="stat-card"><span>Files</span><strong>{uploadedCount}</strong></div>
 <div className="stat-card"><span>AI Engine</span><strong>{aiEnabled?"Groq AI":"Rule-based"}</strong></div>
 <div className="stat-card"><span>Language</span><strong>{detectedLanguage||"Waiting"}</strong></div>
 <div className="stat-card"><span>Mode</span><strong>{documentationMode || "Smart Docs"}</strong></div>
 </div>
 <DevFlowDocumentView
 content={doc ? cleanDoc(doc) : ""}
 emptyText={`Your generated documentation will appear here.\n\nGenerated docs are saved to your workspace automatically.`}
/>
 </section>
 </>)}

 {/* BUG ANALYZER */}
 {activeModule === "bugs" && (<>
 <section className="card">
 <h2>AI Bug Analyzer</h2>
 <p className="helper-text">Paste an error log, traceback, or terminal error and get a clear explanation with suggested fixes.</p>
 <textarea className="code-textarea" value={bugLog} onChange={e=>setBugLog(e.target.value)} placeholder="Paste your error log here..." />
 {bugLoading && <div className="loading-overlay"><div className="loading-card"><InfinitySpin width="220" color="#2563eb"/><h2>Analyzing Bug...</h2><p>Reading the error log...</p></div></div>}
 <div className="button-row">
 <button onClick={handleAnalyzeBug} disabled={bugLoading||!bugLog.trim()}>{bugLoading?"Analyzing...":"Analyze Bug"}</button>
 <button className="danger-btn" onClick={handleClearAll}>Clear</button>
 </div>
 </section>
 <section className="card docs-card">
 <h2>Bug Analysis Result</h2>
 <DevFlowDocumentView
 content={bugAnalysis || ""}
 emptyText="Bug analysis will appear here."
/>
 </section>
 </>)}

 {/* PROJECT HEALTH */}
 {activeModule === "health" && (
 <div className="module-single-view">
 <section className="card docs-card">
 <h2>Smart Health Review</h2>
 <p className="helper-text">Upload or paste code in the Documentation tab first. DevFlow will choose Code Quality, File Health, Project Snapshot, or Full Smart Health automatically.</p>
 <div className="button-row">
 <button onClick={handleProjectHealth} disabled={loading||!code.trim()}>{loading?"Analyzing...":"Generate Health Review"}</button>
 <button className="secondary-btn" onClick={saveHealthReport} disabled={!healthReport||saving||demoMode}>{demoMode ? "Demo Save Off" : saving?"Saving...":" Save Health"}</button>
 <button className="secondary-btn" onClick={()=>{if(!healthReport){toast.error("Generate health report first.");return;}navigator.clipboard.writeText(cleanDevFlowOutput(healthReport));toast.success("Copied!");}} disabled={!healthReport}>Copy</button>
 <button className="secondary-btn" onClick={()=>{if(!healthReport){toast.error("Generate health report first.");return;}downloadTextFile(cleanDevFlowOutput(healthReport),"project-health-report.md","text/markdown");toast.success("Markdown exported!");}} disabled={!healthReport}>Markdown</button>
 <button className="secondary-btn" onClick={()=>exportContentAsPDF("Smart Health Report", healthReport, "project-health-report.pdf")} disabled={!healthReport}>PDF</button>
 <button className="danger-btn" onClick={handleClearAll}>Clear</button>
 </div>
 <DevFlowDocumentView
 content={healthReport || ""}
 emptyText="Smart health review will appear here."
/>
 </section>
 </div>
 )}

 {/* TASK GENERATOR */}
 {activeModule === "tasks" && (<>
 <section className="card">
 <h2>Team Task Generator</h2>
 <p className="helper-text">Paste client requirements or meeting notes and generate developer-ready tasks.</p>
 <textarea className="code-textarea" value={requirementsText} onChange={e=>setReqText(e.target.value)} placeholder="Paste client requirements, meeting notes, or feature ideas here..." />
 {taskLoading && <div className="loading-overlay"><div className="loading-card"><InfinitySpin width="220" color="#2563eb"/><h2>Generating Tasks...</h2><p>Breaking down requirements...</p></div></div>}
 <div className="button-row">
 <button onClick={handleGenerateTasks} disabled={taskLoading||!requirementsText.trim()}>{taskLoading?"Generating...":"Generate Tasks"}</button>
 <button className="danger-btn" onClick={handleClearAll}>Clear</button>
 </div>
 </section>
 <section className="card docs-card">
 <h2>Generated Tasks</h2>
 <DevFlowDocumentView
 content={taskPlan || ""}
 emptyText="Generated team tasks will appear here."
/>
 </section>
 </>)}

 {/* SAVED DOCS HISTORY */}
 {activeModule === "history" && (
 <div className="module-single-view">
 <section className="card docs-card">
 {!selectedDoc ? (
 <>
 <h2>Saved Documentation - {workspace.name}</h2>
 <p className="helper-text">Open saved documentation, GitHub repo reports, export them, or delete old records.</p>

 {docsLoading ? (
 <p style={{color:"#64748b"}}>Loading saved documents...</p>
 ) : savedDocs.length === 0 ? (
 <p style={{color:"#64748b"}}>No saved documents yet. Generate documentation or GitHub repo docs, then save them to your workspace.</p>
 ) : (
 <div className="docs-history-list">
 {savedDocs.map(d => (
 <div key={d.id} className="history-item">
 <div className="history-info">
 <strong>{d.title}</strong>
 <span>{d.language} {d.file_count} file{d.file_count!==1?"s":""} {new Date(d.created_at).toLocaleDateString()}</span>
 </div>
 <div style={{display:"flex",gap:"10px",flexWrap:"wrap"}}>
 <button className="secondary-btn small-btn" onClick={() => openSavedDoc(d.id)} disabled={openingDocId === d.id}>
 {openingDocId === d.id ? "Opening..." : "Open"}
 </button>
 <button className="danger-btn small-btn" onClick={() => deleteDoc(d.id)}>Delete</button>
 </div>
 </div>
 ))}
 </div>
 )}
 </>
 ) : (
 <>
 <div style={{display:"flex",justifyContent:"space-between",gap:"14px",alignItems:"flex-start",flexWrap:"wrap"}}>
 <div>
 <h2 style={{marginBottom:"6px"}}>{selectedDoc.title}</h2>
 <p className="helper-text" style={{marginTop:0}}>
 {selectedDoc.language || "Unknown"} {selectedDoc.file_count || 1} file{selectedDoc.file_count!==1?"s":""} Saved {new Date(selectedDoc.created_at).toLocaleDateString()}
 </p>
 </div>
 <button className="secondary-btn" onClick={() => setSelectedDoc(null)}> Back to Saved Docs</button>
 </div>

 <div className="button-row" style={{marginTop:"18px"}}>
 <button className="secondary-btn" onClick={() => {navigator.clipboard.writeText(cleanDoc(selectedDoc.content)); toast.success("Copied!");}}>Copy</button>
 <button className="secondary-btn" onClick={() => {downloadTextFile(selectedDoc.content, `${selectedDoc.title || "devflow-documentation"}.md`.replace(/[^a-z0-9_.-]/gi, "-"), "text/markdown"); toast.success("Markdown exported!");}}>Markdown</button>
 <button className="secondary-btn" onClick={() => exportContentAsPDF(selectedDoc.title || "Saved Documentation", selectedDoc.content, `${selectedDoc.title || "devflow-documentation"}.pdf`.replace(/[^a-z0-9_.-]/gi, "-"))}>PDF</button>
 <button className="danger-btn" onClick={() => deleteDoc(selectedDoc.id)}>Delete</button>
 </div>

 <DevFlowDocumentView
 content={selectedDoc.content ? cleanDoc(selectedDoc.content) : ""}
 emptyText="No document content found."
/>
 </>
 )}
 </section>
 </div>
 )}

 {/* GITHUB */}
 {activeModule === "github" && (<>
 <section className="card">
 <h2>GitHub Integration</h2>
 <p className="helper-text">Paste a GitHub repository URL and DevFlow will create a fast architecture-level repository report for onboarding, review, and documentation.</p>
 <label className="upload-label">GitHub Repository URL</label>
 <input className="auth-input" style={{marginBottom:"12px",fontFamily:"monospace"}} placeholder="https://github.com/username/reponame" value={repoUrl} onChange={e=>setRepoUrl(e.target.value)} />
 <label className="upload-label">GitHub Token (optional for private repos)</label>
 <input className="auth-input" style={{marginBottom:"12px",fontFamily:"monospace"}} placeholder="ghp_xxxxxxxxxxxx (leave empty for public repos)" type="password" value={githubToken} onChange={e=>setGithubToken(e.target.value)} />
 <p className="helper-text">For private repos: go to GitHub Settings Developer Settings Personal Access Tokens Generate new token (classic) check <strong>repo</strong> scope.</p>
 {repoLoading && <div className="loading-overlay"><div className="loading-card"><InfinitySpin width="220" color="#2563eb"/><h2>Fetching Repository...</h2><p>Scanning key files and generating a fast repository summary...</p></div></div>}
 <div className="button-row">
 <button onClick={handleGithubDocument} disabled={repoLoading||!repoUrl.trim()}>{repoLoading?"Fetching...":"Generate Fast Repo Docs"}</button>
 <button className="secondary-btn" onClick={()=>{if(!repoDoc){toast.error("Generate GitHub docs first.");return;}navigator.clipboard.writeText(cleanDoc(repoDoc));toast.success("Copied!");}} disabled={!repoDoc}>Copy</button>
 <button className="secondary-btn" onClick={()=>{if(!repoDoc){toast.error("Generate GitHub docs first.");return;}downloadTextFile(repoDoc,"github-repository-documentation.md","text/markdown");toast.success("Markdown exported!");}} disabled={!repoDoc}>Markdown</button>
 <button className="secondary-btn" onClick={()=>exportContentAsPDF("GitHub Repository Documentation", repoDoc, "github-repository-documentation.pdf")} disabled={!repoDoc}>PDF</button>
 <button className="secondary-btn" onClick={saveRepoDoc} disabled={!repoDoc||saving}>{demoMode ? "Demo Save Off" : saving?"Saving...":" Save"}</button>
 <button className="danger-btn" onClick={()=>{setRepoUrl("");setRepoDoc("");setRepoName("");}}>Clear</button>
 </div>
 </section>
 <section className="card docs-card">
 <h2>Repository Documentation{repoName && <span style={{fontSize:"14px",color:"#64748b",marginLeft:"10px"}}> {repoName}</span>}</h2>
 <DevFlowDocumentView
 content={repoDoc || ""}
 emptyText={`Paste a GitHub URL and click Generate Docs.\n\nSupports:\n- Public repositories\n- Private repos with token\n- Any language`}
/>
 </section>
 </>)}

 {/* BILLING / USAGE */}
 {activeModule === "billing" && (
 <div className="module-single-view">
 <section className="card billing-page">
 <div className="billing-hero">
 <div>
 <span className="eyebrow">Phase 4 Billing</span>
 <h2>Plan & Subscription</h2>
 <p>Upgrade DevFlow with Stripe Checkout, manage billing in Stripe Customer Portal, and keep workspace limits aligned with the active plan.</p>
 </div>
 <div className={`plan-pill plan-${currentPlan}`}>
 <span>Current plan</span>
 <strong>{formatPlanName(usageInfo?.plan)}</strong>
 </div>
 </div>

 <div className="billing-summary-grid">
 <div className="billing-summary-card">
 <span>Plan</span>
 <strong>{formatPlanName(usageInfo?.plan)}</strong>
 </div>
 <div className="billing-summary-card">
 <span>Billing period</span>
 <strong>{usageInfo?.period || ""}</strong>
 </div>
 <div className="billing-summary-card">
 <span>Subscription status</span>
 <strong>{String(subscriptionStatus).replaceAll("_", " ")}</strong>
 </div>
 <div className="billing-summary-card">
 <span>Renews / access through</span>
 <strong>{periodEndLabel}</strong>
 </div>
 </div>

 <div className={`billing-status-card ${billing.stripe_configured ? "ready" : "warning"}`}>
 <div className="status-icon">{billing.stripe_configured ? "" : "!"}</div>
 <div>
 <strong>{billing.stripe_configured ? "Stripe is configured" : "Stripe is not configured yet"}</strong>
 <p>
 {billing.stripe_configured
 ? "Users can upgrade through Stripe Checkout and manage subscription changes from the Stripe Customer Portal."
 : "Add STRIPE_SECRET_KEY, STRIPE_PRO_PRICE_ID, and STRIPE_TEAM_PRICE_ID in your backend .env file to enable real payments."}
 </p>
 </div>
 </div>

 <div className="pricing-grid premium-pricing-grid">
 {billingPlanCards.map(card => {
 const isCurrent = card.plan === currentPlan;
 const isPaidCard = card.plan !== "free";
 const isFeatured = card.plan === "pro";

 return (
 <div
 key={card.plan}
 className={`pricing-card ${isFeatured ? "featured" : ""} ${isCurrent ? "current" : ""}`}
 >
 <div className="pricing-card-top">
 <div>
 <span className="pricing-badge">{card.badge}</span>
 <h3>{card.title}</h3>
 </div>
 {isCurrent && <span className="current-badge">Active</span>}
 </div>

 <div className="price-line">
 <strong>{card.price}</strong>
 <span>{card.suffix}</span>
 </div>

 <p className="pricing-description">{card.description}</p>

 <ul className="pricing-list">
 {card.items.map(item => <li key={item}>{item}</li>)}
 </ul>

 {isCurrent ? (
 <button className="pricing-btn muted" disabled>Current Plan</button>
 ) : isPaidCard ? (
 <button className="pricing-btn" onClick={() => startStripeCheckout(card.plan)} disabled={billingLoading}>
 {billingLoading ? "Opening Stripe..." : `Upgrade to ${card.title}`}
 </button>
 ) : (
 <button className="pricing-btn muted" disabled>Automatic after cancellation</button>
 )}
 </div>
 );
 })}
 </div>

 <div className="billing-actions">
 <button className="secondary-btn" onClick={loadUsage} disabled={usageLoading}>
 {usageLoading ? "Refreshing..." : "Refresh Billing"}
 </button>
 <button className="secondary-btn" onClick={refreshStripeSubscription} disabled={billingLoading || !billing.stripe_subscription_id}>
 {billingLoading ? "Syncing..." : "Sync Stripe Status"}
 </button>
 <button onClick={openCustomerPortal} disabled={billingLoading || !billing.stripe_customer_id}>
 {billingLoading ? "Opening..." : "Manage Stripe Subscription"}
 </button>
 </div>

 <div className="billing-note">
 <strong>Production note</strong>
 <p>Stripe Checkout handles upgrades, Stripe Customer Portal handles cancellation and payment changes, and Stripe webhooks plus manual sync keep Supabase billing status accurate.</p>
 </div>
 </section>
 </div>
 )}

 {/* TEAM */}
 {activeModule === "team" && (
 <div className="module-single-view">
 <section className="card docs-card">
 <h2>Team - {workspace.name}</h2>
 <p className="helper-text">Invite team members to collaborate on this workspace. They need to have a DevFlow account first.</p>
 <div className="invite-row">
 <input className="auth-input" style={{marginBottom:0,flex:1}} placeholder="Enter teammate's email address" value={inviteEmail} onChange={e=>setInviteEmail(e.target.value)} onKeyDown={e=>e.key==="Enter"&&inviteMember()} />
 <button onClick={inviteMember} disabled={inviting} style={{whiteSpace:"nowrap"}}>{inviting?"Inviting...":"Invite Member"}</button>
 </div>
 <div style={{marginTop:"24px",padding:"16px",background:"#f8fafc",borderRadius:"12px"}}>
 <p style={{margin:0,color:"#374151",fontWeight:"bold"}}>Workspace Info</p>
 <p style={{margin:"8px 0 0",color:"#64748b",fontSize:"14px"}}>Name: {workspace.name}</p>
 <p style={{margin:"4px 0 0",color:"#64748b",fontSize:"14px"}}>Your role: {workspace.role || "owner"}</p>
 </div>
 </section>
 </div>
 )}

 </main>
 </div>
 );
}

// 
// ROOT controls which screen to show
// 
export default function App() {
 const [user, setUser] = useState(null);
 const [workspace, setWorkspace] = useState(null);
 const [checkingSession, setCheckingSession] = useState(!PUBLIC_DEMO_MODE);
 const [showAuth, setShowAuth] = useState(false);
 const [authMode, setAuthMode] = useState("login");
 const didBootstrap = useRef(false);

 useEffect(() => {
 if (PUBLIC_DEMO_MODE) return;
 if (didBootstrap.current) return;
 didBootstrap.current = true;

 const boot = async () => {
 const token = getToken();
 const storedUser = getStoredUser();

 if (!token || !storedUser) {
 clearSession();
 setShowAuth(false);
 setCheckingSession(false);
 return;
 }

 try {
 let response = await fetch(`${API_BASE_URL}/auth/me`, { headers: authHeaders() });
 let data = await safeJson(response);

 if (response.status === 401) {
 const refreshed = await refreshSession();
 if (!refreshed) {
 clearSession();
 setUser(null);
 setWorkspace(null);
 setCheckingSession(false);
 return;
 }
 response = await fetch(`${API_BASE_URL}/auth/me`, { headers: authHeaders() });
 data = await safeJson(response);
 }

 if (response.ok && data.user) {
 setSession({ user: data.user });
 setUser(data.user);
 } else {
 clearSession();
 setUser(null);
 }
 } catch {
 clearSession();
 setUser(null);
 } finally {
 setCheckingSession(false);
 }
 };

 boot();
 }, []);

 const handleLogin = u => {
 setUser(u);
 setWorkspace(null);
 setShowAuth(false);
 };

 const openAuth = (mode = "login") => {
 if (PUBLIC_DEMO_MODE) {
 setUser(DEMO_USER);
 setWorkspace(DEMO_WORKSPACE);
 setShowAuth(false);
 return;
 }
 setAuthMode(mode);
 setShowAuth(true);
 };

 const handleLogout = () => {
 clearSession();
 setUser(null);
 setWorkspace(null);
 setShowAuth(false);
 };

 const handleAuthExpired = () => {
 clearSession();
 setUser(null);
 setWorkspace(null);
 toast.error("Session expired. Please log in again.");
 };

 if (PUBLIC_DEMO_MODE) {
 return (
 <>
 <Toaster position="top-right" />
 <MainApp
 user={DEMO_USER}
 workspace={DEMO_WORKSPACE}
 onSwitchWorkspace={() => {}}
 onLogout={() => {}}
 onAuthExpired={() => {}}
 />
 </>
 );
 }

 if (checkingSession) {
 return (
 <>
 <Toaster position="top-right" />
 <div className="auth-screen">
 <div className="auth-card">
 <h1 className="auth-logo">DevFlow</h1>
 <p className="auth-tagline">Checking your session...</p>
 </div>
 </div>
 </>
 );
 }

 if (!user && !showAuth) {
 return (
 <>
 <Toaster position="top-right" />
 <LandingPage onStart={openAuth} />
 </>
 );
 }

 if (!user) {
 return (
 <>
 <Toaster position="top-right" />
 <AuthScreen
 onLogin={handleLogin}
 initialMode={authMode}
 onBackToLanding={() => setShowAuth(false)}
 />
 </>
 );
 }

 if (!workspace) return (<><Toaster position="top-right"/><WorkspaceSelector user={user} onSelect={setWorkspace} onLogout={handleLogout} onAuthExpired={handleAuthExpired}/></>);
 return (<><Toaster position="top-right"/><MainApp user={user} workspace={workspace} onSwitchWorkspace={()=>setWorkspace(null)} onLogout={handleLogout} onAuthExpired={handleAuthExpired}/></>);
}

