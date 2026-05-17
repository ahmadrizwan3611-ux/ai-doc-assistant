import React, { useMemo, useState } from "react";
import "./App.css";
import jsPDF from "jspdf";
import toast, { Toaster } from "react-hot-toast";
import { InfinitySpin } from "react-loader-spinner";

const API_BASE_URL = process.env.REACT_APP_API_URL || "https://ai-doc-assistant-production-7946.up.railway.app";
const MAX_FILE_SIZE_MB = 2;
const MAX_TOTAL_FILES = 80;

function App() {
  const [code, setCode] = useState("");
  const [doc, setDoc] = useState("");
  const [loading, setLoading] = useState(false);
  const [fileName, setFileName] = useState("");
  const [detectedLanguage, setDetectedLanguage] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [notification, setNotification] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [uploadedCount, setUploadedCount] = useState(0);
  const [aiEnabled, setAiEnabled] = useState(false);
  const [selectedFileCount, setSelectedFileCount] = useState(0);
  const [darkMode, setDarkMode] = useState(false);

  const selectedSummary = useMemo(() => {
    if (!uploadedCount && !fileName) return "No files selected yet.";
    if (selectedFileCount === 1) return `1 file selected: ${fileName}`;
    return `${selectedFileCount} files selected.`;
  }, [uploadedCount, selectedFileCount, fileName]);

  const showNotification = (message) => {
    setNotification(message);
    setTimeout(() => setNotification(""), 2500);
  };

  const showError = (message) => {
    setErrorMessage(message);
    setTimeout(() => setErrorMessage(""), 4000);
  };

  const shouldIgnoreFile = (filePath) => {
    const path = filePath.toLowerCase();

    const ignoredParts = [
      "node_modules",
      "venv",
      ".venv",
      "__pycache__",
      ".git",
      "dist",
      "build",
      ".next",
      "coverage",
      "images",
      "image",
      "assets",
      "media",
      ".cache"
    ];

    const ignoredFiles = [
      "package-lock.json",
      "yarn.lock",
      "pnpm-lock.yaml",
      ".env",
      ".env.local",
      ".gitignore",
      ".ds_store"
    ];

    const ignoredExtensions = [
      ".png",
      ".jpg",
      ".jpeg",
      ".gif",
      ".svg",
      ".ico",
      ".pdf",
      ".zip",
      ".rar",
      ".exe",
      ".dll",
      ".mp4",
      ".mp3",
      ".woff",
      ".woff2",
      ".ttf"
    ];

    if (ignoredParts.some((part) => path.includes(part))) return true;
    if (ignoredFiles.some((file) => path.endsWith(file))) return true;
    if (ignoredExtensions.some((ext) => path.endsWith(ext))) return true;

    return false;
  };

  const readFileAsText = (file) => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();

      reader.onload = (event) => {
        resolve({
          name: file.webkitRelativePath || file.name,
          content: event.target.result || ""
        });
      };

      reader.onerror = () => reject(new Error(`Failed to read ${file.name}`));
      reader.readAsText(file);
    });
  };

  const handleFileUpload = async (event) => {
    setErrorMessage("");

    const selectedFiles = Array.from(event.target.files || []);

    const supportedFiles = selectedFiles.filter((file) => {
      const filePath = file.webkitRelativePath || file.name;
      const isTooLarge = file.size > MAX_FILE_SIZE_MB * 1024 * 1024;
      return !shouldIgnoreFile(filePath) && !isTooLarge;
    });
    setSelectedFileCount(supportedFiles.length);

    if (supportedFiles.length === 0) {
      setFileName("");
      setUploadedCount(0);
      setCode("");
      setDoc("No supported code files found. Please upload source code files.");
      setDetectedLanguage("");
      showError("No supported code files found.");
      return;
    }

    const filesToRead = supportedFiles.slice(0, MAX_TOTAL_FILES);

    try {
      const results = await Promise.all(filesToRead.map(readFileAsText));

      const combinedCode = results
        .map((file) => `--- FILE: ${file.name} ---\n${file.content}`)
        .join("\n\n");

      setCode(combinedCode);
      setDoc("");
      setDetectedLanguage("");
      setUploadedCount(results.length);
      setFileName(results.map((file) => file.name).join(", "));

      if (supportedFiles.length > MAX_TOTAL_FILES) {
        showNotification(`Loaded first ${MAX_TOTAL_FILES} files only.`);
      } else {
        showNotification("Files loaded successfully.");
      }
    } catch (error) {
      showError(error.message || "Failed to read selected files.");
    } finally {
      event.target.value = "";
    }
  };

  const handleDrop = async (event) => {
    event.preventDefault();
    event.stopPropagation();
    setIsDragging(false);
    setErrorMessage("");

    try {
      const droppedFiles = Array.from(event.dataTransfer.files || []);

      if (!droppedFiles.length) {
        toast.error("No files dropped.");
        return;
      }

      await processSelectedFiles(droppedFiles);
    } catch (error) {
      console.error("Drop error:", error);
      toast.error("Failed to read dropped files.");
    }
  };

  const cleanDocumentationText = (text) => {
    return String(text || "")
      .replace(/```python/g, "")
      .replace(/```javascript/g, "")
      .replace(/```/g, "")
      .replace(/### /g, "")
      .replace(/## /g, "")
      .replace(/`/g, "");
  };

  const handleCopyDoc = async () => {
    if (!doc) {
      toast.error("Please generate documentation first.");
      return;
    }

    try {
      await navigator.clipboard.writeText(cleanDocumentationText(doc));
      toast.success("Documentation copied!");
    } catch (error) {
      toast.error("Failed to copy documentation.");
    }
  };

  const handleGenerateDoc = async () => {
    if (!code.trim()) {
      showError("Please paste code or upload a project first.");
      return;
    }

    setLoading(true);
    setDoc("");
    setDetectedLanguage("");
    setErrorMessage("");

    try {
      const response = await fetch(`${API_BASE_URL}/generate-doc`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          code,
          fileName
        })
      });

      const data = await response.json();

      if (!response.ok || data.error) {
        throw new Error(data.error || "Something went wrong.");
      }

      setDoc(data.doc || "No documentation returned.");
      setDetectedLanguage(data.language || "");
      setAiEnabled(Boolean(data.aiEnabled));
      showNotification("Documentation generated successfully.");
    } catch (error) {
      setDoc("");
      showError(error.message || "Failed to connect with backend. Make sure Flask is running.");
    } finally {
      setLoading(false);
    }
  };

  const downloadTextFile = (content, fileNameToDownload, type) => {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileNameToDownload;
    link.click();
    URL.revokeObjectURL(url);
  };

  const handleExportMarkdown = () => {
    if (!doc) {
      toast.error("Please generate documentation first.");
      return;
    }

    downloadTextFile(doc, "documentation.md", "text/markdown");
    toast.success("Markdown exported!");
  };

  const handleExportReadme = () => {
    if (!doc) {
      toast.error("Please generate documentation first.");
      return;
    }

    const readmeContent =
      "# Project Documentation\n\n" +
      "Generated by AI Doc Assistant\n\n" +
      "---\n\n" +
      doc;

    downloadTextFile(readmeContent, "README.md", "text/markdown");
    toast.success("README exported!");
  };

  const handleExportPDF = () => {
    if (!doc) {
      toast.error("Please generate documentation first.");
      return;
    }

    const pdf = new jsPDF("p", "mm", "a4");
    const pageWidth = pdf.internal.pageSize.getWidth();
    const pageHeight = pdf.internal.pageSize.getHeight();
    const margin = 14;
    const maxLineWidth = pageWidth - margin * 2;
    let y = 18;
    let pageNumber = 1;

    const addFooter = () => {
      pdf.setFont("helvetica", "normal");
      pdf.setFontSize(8);
      pdf.text(`Page ${pageNumber}`, pageWidth - margin - 15, pageHeight - 8);
    };

    pdf.setFont("helvetica", "bold");
    pdf.setFontSize(18);
    pdf.text("AI Doc Assistant", margin, y);
    y += 8;

    pdf.setFont("helvetica", "normal");
    pdf.setFontSize(10);
    pdf.text("Generated Documentation", margin, y);
    y += 10;

    pdf.setFont("courier", "normal");
    pdf.setFontSize(9);

    const cleanDoc = cleanDocumentationText(doc);
    const lines = pdf.splitTextToSize(cleanDoc, maxLineWidth);

    lines.forEach((line) => {
      if (y > pageHeight - 15) {
        addFooter();
        pdf.addPage();
        pageNumber += 1;
        y = 18;
        pdf.setFont("courier", "normal");
        pdf.setFontSize(9);
      }

      pdf.text(line, margin, y);
      y += 5;
    });

    addFooter();
    pdf.save("documentation.pdf");
    toast.success("PDF exported!");
  };

  const handleClearAll = () => {
    setCode("");
    setDoc("");
    setFileName("");
    setDetectedLanguage("");
    setUploadedCount(0);
    setAiEnabled(false);
    setErrorMessage("");
    showNotification("Cleared.");
    setSelectedFileCount(0);
  };

  return (
   <>
     <Toaster position="top-right" />
      <div className={`app-container ${darkMode ? "dark-mode" : ""}`}>
      {notification && <div className="toast-notification">{notification}</div>}
      {errorMessage && <div className="toast-notification error-toast">{errorMessage}</div>}

      <header className="hero-section">
        <h1>AI Doc Assistant</h1>
          <button
            className="theme-toggle"
            onClick={() => setDarkMode(!darkMode)}
          >
            {darkMode ? "☀️ Light Mode" : "🌙 Dark Mode"}
          </button>
        <p>Generate clean project documentation from source code, files, or full project folders.</p>
      </header>

      <main className="main-grid">
        <section className="card">
          <h2>Paste or Upload Project Code</h2>
         <div className="upload-module">
          <label className="upload-label">Upload single or multiple files</label>
          <input
            type="file"
            className="file-input"
            onChange={handleFileUpload}
            accept=".py,.js,.jsx,.ts,.tsx,.html,.css,.sql,.php,.java,.cpp,.c,.cs,.kt,.swift,.dart,.txt,.md,.json"
            multiple
          />
          </div>
          <div className="upload-module">
          <label className="upload-label">Upload full project folder securely</label>
          <input
            type="file"
            className="file-input"
            onChange={handleFileUpload}
            webkitdirectory="true"
            directory="true"
            multiple
          />
          </div>
            <p className="helper-text">
              Your browser may ask for permission before uploading a folder. This is normal.
            </p>
            <p className="helper-text">
              Only source code files are processed. System folders, images, videos, build files, and environment files are ignored.
            </p>

          <p className="file-name">
            {selectedSummary}
          </p>

            <div
              className={`drop-zone ${isDragging ? "drag-active" : ""}`}
              onDragEnter={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setIsDragging(true);
              }}
              onDragOver={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setIsDragging(true);
              }}
              onDragLeave={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setIsDragging(false);
              }}
              onDrop={handleDropUpload}
            >
              <strong>Drag & drop source files here</strong>
              <span>or use the upload buttons above</span>
            </div>

          <textarea
            className="code-textarea"
            value={code}
            onChange={(event) => {
              setCode(event.target.value);
              setDoc("");
              setDetectedLanguage("");
            }}
              placeholder={`No documentation generated yet.

          Upload files or paste code, then click Generate.

          Supported:
          - Single code snippets
          - Full files
          - Multiple files
          - Project folders`}
          />

            {loading && (
              <div className="loading-overlay">
                <div className="loading-card">
                  <InfinitySpin width="220" color="#2563eb" />

                  <h2>Analyzing Project...</h2>

                  <p>
                    Scanning files, detecting frameworks, generating
                    architecture summaries, and preparing documentation.
                  </p>
                </div>
              </div>
            )}

          <div className="button-row">
            <button onClick={handleGenerateDoc} disabled={loading || !code.trim()}>
              {loading ? "Generating..." : "Generate"}
            </button>

            <button className="secondary-btn" onClick={handleCopyDoc} disabled={!doc || loading}>
              Copy
            </button>

            <button className="secondary-btn" onClick={handleExportMarkdown} disabled={!doc || loading}>
              Markdown
            </button>

            <button className="secondary-btn" onClick={handleExportReadme} disabled={!doc || loading}>
              README
            </button>

            <button className="secondary-btn" onClick={handleExportPDF} disabled={!doc || loading}>
              PDF
            </button>

            <button className="danger-btn" onClick={handleClearAll} disabled={loading}>
              Clear
            </button>
          </div>
        </section>
        
          <section className="card docs-card">
          <h2>Generated Documentation</h2>
            <div className="stats-grid">
              <div className="stat-card">
                <span>Files</span>
                <strong>{uploadedCount}</strong>
              </div>

              <div className="stat-card">
                <span>AI Engine</span>
                <strong>{aiEnabled ? "Enabled" : "Rule-based"}</strong>
              </div>

              <div className="stat-card">
                <span>Language</span>
                <strong>{detectedLanguage || "Waiting"}</strong>
              </div>
            </div>
          <pre className={`output-box ${!doc ? "empty-output" : ""}`}>
            {doc
              ? cleanDocumentationText(doc)
              : `Your generated documentation will appear here.\n\nSupported:\n- Single code snippets\n- Full files\n- Multiple files\n- Project folders\n\nClick "Generate" to start.`}
          </pre>
        </section>
      </main>
    </div>
    </>
  );
}

export default App;
