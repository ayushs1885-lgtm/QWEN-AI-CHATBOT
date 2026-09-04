"use client";

import React, { useState, useRef, useEffect } from "react";
import {
  MessageSquare,
  Plus,
  Home,
  FileText,
  Wrench,
  Code,
  Settings,
  Upload,
  Paperclip,
  Send,
  Activity,
  Moon,
  Sun,
  Copy,
  ThumbsUp,
  ThumbsDown,
  Globe,
  Edit2,
  Check,
  Search,
  Terminal,
  Cpu,
  Zap,
  Sliders,
  FolderOpen,
  AlertTriangle
} from "lucide-react";

interface OcrExtractedData {
  orderId?: string;
  date?: string;
  amount?: string;
  productInfo?: string;
  errorCode?: string;
  confidenceScore?: number;
  hasConflict?: boolean;
  conflictReason?: string;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  time: string;
  latency?: number;
  isError?: boolean;
  extractedData?: OcrExtractedData;
}

const QwenLogo = ({ size = "w-6 h-6" }: { size?: string }) => (
  <svg viewBox="0 0 100 100" className={`${size} fill-none`}>
    <path
      d="M50 10 L85 30 L85 70 L50 90 L15 70 L15 30 Z"
      stroke="currentColor"
      strokeWidth="6"
      className="text-indigo-400"
    />
    <path
      d="M50 10 L50 50 L85 70 M50 50 L15 70"
      stroke="currentColor"
      strokeWidth="5"
      className="text-purple-400"
    />
    <circle cx="50" cy="50" r="12" fill="url(#grad)" />
    <defs>
      <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor="#818cf8" />
        <stop offset="100%" stopColor="#c084fc" />
      </linearGradient>
    </defs>
  </svg>
);

const RobotMascot = () => (
  <div className="relative w-16 h-16 flex-shrink-0 flex items-center justify-center">
    <div className="absolute inset-0 bg-indigo-500/20 rounded-full blur-xl animate-pulse" />
    <svg viewBox="0 0 120 120" className="w-full h-full relative z-10 drop-shadow-[0_10px_20px_rgba(99,102,241,0.4)]">
      <rect x="22" y="52" width="8" height="16" rx="4" fill="#6366f1" />
      <rect x="90" y="52" width="8" height="16" rx="4" fill="#6366f1" />
      <rect x="28" y="30" width="64" height="58" rx="28" fill="url(#botHeadGrad)" stroke="#818cf8" strokeWidth="2.5" />
      <rect x="36" y="38" width="48" height="38" rx="18" fill="#090d20" stroke="#4f46e5" strokeWidth="1.5" />
      <circle cx="50" cy="55" r="6" fill="#a855f7">
        <animate attributeName="r" values="6;7;6" dur="2s" repeatCount="indefinite" />
      </circle>
      <circle cx="70" cy="55" r="6" fill="#a855f7">
        <animate attributeName="r" values="6;7;6" dur="2s" repeatCount="indefinite" />
      </circle>
      <circle cx="52" cy="53" r="2" fill="#ffffff" />
      <circle cx="72" cy="53" r="2" fill="#ffffff" />
      <path d="M 52 68 Q 60 74 68 68" stroke="#818cf8" strokeWidth="2.5" strokeLinecap="round" fill="none" />
      <path d="M 40 88 C 40 88, 60 98, 80 88 L 76 102 C 76 102, 60 108, 44 102 Z" fill="url(#botBodyGrad)" />
      <defs>
        <linearGradient id="botHeadGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#2e1065" />
          <stop offset="50%" stopColor="#3b0764" />
          <stop offset="100%" stopColor="#1e1b4b" />
        </linearGradient>
        <linearGradient id="botBodyGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#4c1d95" />
          <stop offset="100%" stopColor="#1e1b4b" />
        </linearGradient>
      </defs>
    </svg>
  </div>
);

// Atmospheric Glowing Planet Background graphic matching the requested UI design
const CosmicPlanetBackground = () => (
  <div className="absolute inset-0 pointer-events-none overflow-hidden z-0">
    {/* Atmospheric outer glow */}
    <div className="absolute -bottom-40 -right-20 w-[650px] h-[650px] rounded-full bg-gradient-to-tr from-indigo-900/40 via-purple-600/20 to-blue-500/30 blur-3xl opacity-75" />
    
    {/* Planet curvature sphere */}
    <div className="absolute -bottom-72 -right-36 w-[800px] h-[800px] rounded-full bg-[#050819] border border-indigo-400/20 shadow-[0_-25px_80px_rgba(99,102,241,0.25)] opacity-90" />
    
    {/* Glowing horizon crest curve */}
    <div className="absolute -bottom-72 -right-36 w-[800px] h-[800px] rounded-full border-t-2 border-indigo-300/60 shadow-[0_-10px_30px_rgba(168,85,247,0.8)]" />

    {/* Distant ambient nebula stars */}
    <div className="absolute top-1/4 right-1/3 w-1 h-1 bg-white rounded-full shadow-[0_0_8px_white] opacity-80" />
    <div className="absolute top-1/3 right-1/4 w-1.5 h-1.5 bg-indigo-200 rounded-full shadow-[0_0_10px_indigo] opacity-60" />
    <div className="absolute top-1/2 right-1/2 w-1 h-1 bg-purple-300 rounded-full shadow-[0_0_6px_purple] opacity-70" />
  </div>
);

export default function QwenDashboard() {
  const [activeTab, setActiveTab] = useState<string>("Chats");
  const [input, setInput] = useState<string>("");
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [lastLatency, setLastLatency] = useState<number | null>(null);
  const [failureCount, setFailureCount] = useState<number>(0);
  const [uploadedFiles, setUploadedFiles] = useState<string[]>([]);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [darkMode, setDarkMode] = useState<boolean>(true);
  const [userName, setUserName] = useState<string>("Guest");

  // Settings State
  const [temperature, setTemperature] = useState<number>(0.7);
  const [maxTokens, setMaxTokens] = useState<number>(1024);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [messages, setMessages] = useState<Message[]>([]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const savedName = localStorage.getItem("qwen_user_name");
      if (savedName) {
        setUserName(savedName);
      } else {
        const defaultName = "Guest";
        localStorage.setItem("qwen_user_name", defaultName);
        setUserName(defaultName);
      }
    }
  }, []);

  // Automatic Smooth Scrolling
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  const handleChangeName = () => {
    if (typeof window !== "undefined") {
      const newName = window.prompt("Change your name:", userName);
      if (newName && newName.trim()) {
        localStorage.setItem("qwen_user_name", newName.trim());
        setUserName(newName.trim());
      }
    }
  };

  const handleSend = async (customQuery?: string) => {
    const queryToSend = customQuery || input;
    if (!queryToSend.trim() || isLoading) return;

    const startTime = performance.now();
    const currentTime = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const userMsg: Message = { role: "user", content: queryToSend, time: currentTime };

    setMessages((prev) => [...prev, userMsg]);
    if (!customQuery) setInput("");
    setIsLoading(true);

    // EXACT CHECK: If user asks who made you
    const cleanedQuery = queryToSend.trim().toLowerCase();
    if (
      cleanedQuery.includes("who made u") || 
      cleanedQuery.includes("who made you") || 
      cleanedQuery.includes("who created you") || 
      cleanedQuery.includes("who created u")
    ) {
      const endTime = performance.now();
      const calculatedLatency = Number(((endTime - startTime) / 1000).toFixed(2));
      setLastLatency(calculatedLatency);

      const botMsg: Message = {
        role: "assistant",
        content: "I was made by AYUSH.",
        time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        latency: calculatedLatency,
      };

      setMessages((prev) => [...prev, botMsg]);
      setIsLoading(false);
      return;
    }

    try {
      const formData = new FormData();
      formData.append("message", queryToSend);
      formData.append("active_tab", activeTab);

      const res = await fetch("http://localhost:8000/api/analyze", {
        method: "POST",
        body: formData,
      });

      const endTime = performance.now();
      const calculatedLatency = Number(((endTime - startTime) / 1000).toFixed(2));

      if (!res.ok) throw new Error("Backend Error");

      const data = await res.json();
      const finalLatency = data.latency || calculatedLatency;

      setLastLatency(finalLatency);

      const botMsg: Message = {
        role: "assistant",
        content: data.reply || data.response || "Response received.",
        time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        latency: finalLatency,
        extractedData: data.extractedData,
      };

      setMessages((prev) => [...prev, botMsg]);
    } catch (error: any) {
      console.error("Backend Error:", error);
      const endTime = performance.now();
      const calculatedLatency = Number(((endTime - startTime) / 1000).toFixed(2));

      setLastLatency(calculatedLatency);
      setFailureCount((prev) => prev + 1);

      const errorMsg: Message = {
        role: "assistant",
        content: "⚠️ Failed to connect to Python Backend. Make sure `python main.py` is running on port 8000.",
        time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        latency: calculatedLatency,
        isError: true,
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    const startTime = performance.now();

    const formData = new FormData();
    formData.append("file", file);
    formData.append("message", `File uploaded: ${file.name}`);

    try {
      const res = await fetch("http://localhost:8000/api/analyze", {
        method: "POST",
        body: formData,
      });

      const endTime = performance.now();
      const calculatedLatency = Number(((endTime - startTime) / 1000).toFixed(2));
      setLastLatency(calculatedLatency);

      if (!res.ok) throw new Error("File processing error");

      const data = await res.json();

      setUploadedFiles((prev) => [...prev, file.name]);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: data.reply || `📄 Processed file \`${file.name}\`.`,
          time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          latency: calculatedLatency,
          extractedData: data.extractedData,
        },
      ]);
    } catch (err: any) {
      const endTime = performance.now();
      const calculatedLatency = Number(((endTime - startTime) / 1000).toFixed(2));

      setLastLatency(calculatedLatency);
      setFailureCount((prev) => prev + 1);

      setUploadedFiles((prev) => [...prev, file.name]);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `📄 Attached file \`${file.name}\`. (Failed to extract backend metadata).`,
          time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          latency: calculatedLatency,
          isError: true,
        },
      ]);
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className={`flex h-screen w-screen font-sans overflow-hidden transition-colors duration-300 ${
      darkMode ? "bg-[#060814] text-slate-100" : "bg-slate-100 text-slate-900"
    }`}>
      
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileUpload}
        accept=".png,.jpg,.jpeg,.pdf,.txt"
        className="hidden"
      />

      {/* LEFT SIDEBAR */}
      <aside className={`w-64 border-r p-4 flex flex-col justify-between backdrop-blur-xl z-20 transition-colors duration-300 ${
        darkMode ? "border-indigo-900/30 bg-[#080b1e]/95" : "border-slate-300 bg-white/80 text-slate-800"
      }`}>
        <div className="space-y-6">
          <div className="flex items-center gap-3 px-2">
            <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-indigo-600 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-500/30">
              <QwenLogo size="w-7 h-7" />
            </div>
            <div>
              <h1 className={`font-bold text-lg leading-none tracking-wide ${darkMode ? "text-white" : "text-slate-900"}`}>Qwen AI</h1>
              <span className="text-xs text-indigo-400">Assistant</span>
            </div>
          </div>

          <button
            onClick={() => {
              setActiveTab("Chats");
              setMessages([]);
            }}
            className="w-full py-3 px-4 rounded-xl bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white font-medium text-sm flex items-center justify-center gap-2 shadow-lg shadow-indigo-500/20 transition-all cursor-pointer"
          >
            <Plus className="h-4 w-4" /> New Chat
          </button>

          <nav className="space-y-1">
            {[
              { label: "Home", icon: Home },
              { label: "Chats", icon: MessageSquare },
              { label: "Documents", icon: FileText },
              { label: "AI Tools", icon: Wrench },
              { label: "Code Assistant", icon: Code },
              { label: "Settings", icon: Settings },
            ].map((item) => (
              <button
                key={item.label}
                onClick={() => setActiveTab(item.label)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors cursor-pointer ${
                  activeTab === item.label
                    ? "bg-indigo-600/20 text-indigo-400 border border-indigo-500/30"
                    : darkMode ? "text-slate-400 hover:text-slate-200 hover:bg-white/5" : "text-slate-600 hover:text-slate-900 hover:bg-slate-200/60"
                }`}
              >
                <item.icon className="h-4 w-4" />
                {item.label}
              </button>
            ))}
          </nav>
        </div>

        <div className="space-y-3">
          <div className={`p-2.5 rounded-xl border flex items-center justify-between ${
            darkMode ? "bg-indigo-950/40 border-indigo-500/20" : "bg-slate-200/50 border-slate-300"
          }`}>
            <div className="flex items-center gap-2">
              <QwenLogo size="w-5 h-5" />
              <span className={`text-xs font-semibold ${darkMode ? "text-slate-300" : "text-slate-700"}`}>Qwen 2.5</span>
            </div>
            <span className="text-[10px] px-2 py-0.5 rounded-md bg-indigo-500/20 text-indigo-400 border border-indigo-500/30 font-mono">Model</span>
          </div>

          <button
            onClick={() => setDarkMode(!darkMode)}
            className={`w-full p-2.5 rounded-xl border flex items-center justify-between text-xs transition-all cursor-pointer ${
              darkMode ? "bg-indigo-950/20 border-indigo-500/10 text-slate-400 hover:text-slate-200" : "bg-slate-200/50 border-slate-300 text-slate-600 hover:text-slate-900"
            }`}
          >
            <span className="flex items-center gap-2">
              {darkMode ? <Moon className="h-4 w-4 text-indigo-400" /> : <Sun className="h-4 w-4 text-amber-500" />}
              {darkMode ? "Dark Mode" : "Light Mode"}
            </span>
            <div className={`w-8 h-4 rounded-full p-0.5 flex transition-colors duration-300 ${
              darkMode ? "bg-indigo-600 justify-end" : "bg-slate-400 justify-start"
            }`}>
              <div className="w-3 h-3 bg-white rounded-full shadow-sm" />
            </div>
          </button>

          <div className={`p-2.5 rounded-xl border flex items-center gap-3 ${
            darkMode ? "bg-indigo-950/40 border-indigo-500/20" : "bg-slate-200/50 border-slate-300"
          }`}>
            <div className="h-9 w-9 rounded-full bg-gradient-to-tr from-purple-500 to-indigo-500 flex items-center justify-center text-white font-semibold shadow-inner uppercase">
              {userName.charAt(0)}
            </div>
            <div className="flex-1 min-w-0">
              <div className={`text-sm font-medium truncate ${darkMode ? "text-white" : "text-slate-900"}`}>{userName}</div>
              <div className="text-xs text-slate-400 truncate">User</div>
            </div>
            <button onClick={handleChangeName} className="p-1 hover:text-indigo-400 text-slate-400 transition-colors cursor-pointer" title="Change Name">
              <Edit2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </aside>

      {/* MAIN VIEW AREA */}
      <main className={`flex-1 flex flex-col justify-between p-6 relative overflow-hidden transition-colors duration-300 ${
        darkMode 
          ? "bg-[#060814]"
          : "bg-slate-50"
      }`}>
        {/* Planet Ambient Background Graphic */}
        {darkMode && <CosmicPlanetBackground />}

        <header className="flex justify-between items-start mb-2 z-10">
          <div className="flex items-center gap-4">
            <RobotMascot />
            <div>
              <h2 className={`text-2xl font-bold tracking-tight ${darkMode ? "text-white" : "text-slate-900"}`}>
                Hi {userName}! 👋
              </h2>
              <p className={`text-sm ${darkMode ? "text-indigo-300/80" : "text-indigo-600"}`}>
                {activeTab === "Home" && "Welcome back to your Qwen AI Dashboard."}
                {activeTab === "Chats" && "Conversational AI Assistant active."}
                {activeTab === "Documents" && "Upload and query your personal documents."}
                {activeTab === "AI Tools" && "Access specialized AI utilities and web search."}
                {activeTab === "Code Assistant" && "Generate, debug, and optimize code snippets."}
                {activeTab === "Settings" && "Customize your model parameters and runtime preferences."}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {lastLatency !== null && (
              <span className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border shadow-md ${
                darkMode ? "bg-indigo-950/80 border-indigo-500/40 text-indigo-300" : "bg-white border-slate-300 text-slate-700"
              }`}>
                <Activity className="h-3.5 w-3.5 text-emerald-500" />
                Latency: <span className="text-emerald-500 font-semibold">{lastLatency}s</span>
              </span>
            )}

            <span className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border shadow-md ${
              failureCount > 0 
                ? "bg-rose-950/80 border-rose-500/40 text-rose-300" 
                : darkMode ? "bg-indigo-950/80 border-indigo-500/40 text-slate-400" : "bg-white border-slate-300 text-slate-600"
            }`}>
              <AlertTriangle className={`h-3.5 w-3.5 ${failureCount > 0 ? "text-rose-400 animate-pulse" : "text-slate-400"}`} />
              Failures: <span className={`font-semibold ${failureCount > 0 ? "text-rose-400" : ""}`}>{failureCount}</span>
            </span>

            <button className={`flex items-center gap-2 px-3.5 py-1.5 rounded-full border text-xs font-medium backdrop-blur-md ${
              darkMode ? "bg-indigo-950/60 border-indigo-500/30 text-indigo-200" : "bg-white border-slate-300 text-slate-700"
            }`}>
              <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse"></span>
              {activeTab}
            </button>
          </div>
        </header>

        {/* TAB 1: HOME */}
        {activeTab === "Home" && (
          <div className="flex-1 overflow-y-auto z-10 my-4 space-y-6 pr-1 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
            <div className={`p-6 rounded-2xl border backdrop-blur-md ${darkMode ? "bg-indigo-950/30 border-indigo-500/20" : "bg-white border-slate-200"}`}>
              <h3 className="text-lg font-bold mb-2">Quick Start Suggestions</h3>
              <div className="grid grid-cols-2 gap-4 mt-4">
                {[
                  { title: "Who created you?", prompt: "Who made you?" },
                  { title: "Explain LLMs simply", prompt: "Explain Large Language Models in simple terms." },
                  { title: "Write a Python Script", prompt: "Write a Python script for web scraping with BeautifulSoup." },
                  { title: "Document Analysis", prompt: "Summarize the key takeaways from my uploaded PDF document." },
                ].map((item, idx) => (
                  <button
                    key={idx}
                    onClick={() => {
                      setActiveTab("Chats");
                      handleSend(item.prompt);
                    }}
                    className={`p-4 rounded-xl border text-left transition-all cursor-pointer hover:border-indigo-500 ${
                      darkMode ? "bg-[#0d1230]/70 border-indigo-500/20 hover:bg-indigo-900/30" : "bg-slate-100 border-slate-200 hover:bg-white"
                    }`}
                  >
                    <div className="text-sm font-semibold text-indigo-400">{item.title}</div>
                    <div className="text-xs opacity-70 mt-1">{item.prompt}</div>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* TAB 2: CHATS (Scrollbar removed, self-scrolling enabled) */}
        {activeTab === "Chats" && (
          <div className="flex-1 overflow-y-auto space-y-4 pr-1 my-2 z-10 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
            {messages.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-center p-6 opacity-60">
                <QwenLogo size="w-12 h-12 mb-3" />
                <p className="text-sm font-medium">Type a question or search query below.</p>
              </div>
            ) : (
              messages.map((msg, index) => (
                <div key={`${msg.time}-${index}`} className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}>
                  <div className={`max-w-xl p-4 rounded-2xl backdrop-blur-md border text-sm leading-relaxed ${
                    msg.role === "user"
                      ? "bg-gradient-to-r from-indigo-600/90 to-purple-600/90 border-indigo-400/30 text-white rounded-br-none shadow-lg shadow-indigo-500/10"
                      : msg.isError
                        ? "bg-rose-950/40 border-rose-500/30 text-rose-200 rounded-bl-none shadow-lg shadow-rose-950/20"
                        : darkMode 
                          ? "bg-[#0d1230]/80 border-indigo-500/20 text-slate-200 rounded-bl-none shadow-lg shadow-black/30"
                          : "bg-white border-slate-200 text-slate-800 rounded-bl-none shadow-sm"
                  }`}>
                    {msg.role === "assistant" && (
                      <div className="flex items-center justify-between gap-2 text-xs font-semibold mb-1.5">
                        <div className="flex items-center gap-1.5">
                          <span className={msg.isError ? "text-rose-400 font-bold" : "text-indigo-400 font-bold"}>Qwen AI</span>
                          <span className={`px-1.5 py-0.2 rounded border text-[10px] ${
                            msg.isError ? "bg-rose-500/20 border-rose-400/20 text-rose-300" : "bg-indigo-500/20 border-indigo-400/20 text-indigo-400"
                          }`}>
                            {msg.isError ? "ERROR" : "BOT"}
                          </span>
                        </div>
                        {msg.latency !== undefined && (
                          <span className="text-[10px] opacity-70 font-normal">⏱️ {msg.latency}s</span>
                        )}
                      </div>
                    )}
                    <p className="whitespace-pre-wrap">{msg.content}</p>

                    {msg.extractedData && (
                      <div className="mt-2 p-2.5 rounded-xl bg-black/30 border border-indigo-500/20 text-xs space-y-1">
                        <div className="font-semibold text-indigo-300">Extracted Information:</div>
                        {msg.extractedData.orderId && <div>Order ID: {msg.extractedData.orderId}</div>}
                        {msg.extractedData.amount && <div>Amount: {msg.extractedData.amount}</div>}
                        {msg.extractedData.date && <div>Date: {msg.extractedData.date}</div>}
                      </div>
                    )}
                    
                    {msg.role === "assistant" && !msg.isError && (
                      <div className="flex items-center gap-3 mt-3 pt-2 border-t border-black/5 dark:border-white/5 opacity-60 text-xs">
                        <button className="hover:opacity-100 transition-opacity cursor-pointer"><Copy className="h-3.5 w-3.5" /></button>
                        <button className="hover:opacity-100 transition-opacity cursor-pointer"><ThumbsUp className="h-3.5 w-3.5" /></button>
                        <button className="hover:opacity-100 transition-opacity cursor-pointer"><ThumbsDown className="h-3.5 w-3.5" /></button>
                      </div>
                    )}

                    <div className="flex justify-end items-center gap-2 mt-2 text-[10px] opacity-50">
                      <span>{msg.time}</span>
                      {msg.role === "user" && <Check className="h-3 w-3" />}
                    </div>
                  </div>
                </div>
              ))
            )}
            {isLoading && (
              <div className="text-xs text-indigo-400 animate-pulse flex items-center gap-2">
                <QwenLogo size="w-4 h-4 animate-spin" /> Processing response...
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        )}

        {/* TAB 3: DOCUMENTS */}
        {activeTab === "Documents" && (
          <div className="flex-1 overflow-y-auto z-10 my-4 space-y-4 pr-1 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
            <div className={`p-6 rounded-2xl border backdrop-blur-md ${darkMode ? "bg-indigo-950/30 border-indigo-500/20" : "bg-white border-slate-200"}`}>
              <h3 className="text-lg font-bold mb-2 flex items-center gap-2">
                <FolderOpen className="h-5 w-5 text-indigo-400" /> Uploaded Document Repository
              </h3>
              {uploadedFiles.length === 0 ? (
                <p className="text-sm opacity-60 my-4">No documents uploaded yet. Drag & drop files in the right sidebar or click below to upload.</p>
              ) : (
                <ul className="space-y-2 my-4">
                  {uploadedFiles.map((fname, idx) => (
                    <li key={idx} className={`p-3 rounded-xl border flex justify-between items-center text-sm ${darkMode ? "bg-black/30 border-indigo-500/20" : "bg-slate-100 border-slate-200"}`}>
                      <span className="font-mono text-indigo-300">📄 {fname}</span>
                      <button onClick={() => { setActiveTab("Chats"); handleSend(`Analyze the uploaded document '${fname}'`); }} className="text-xs text-indigo-400 underline hover:text-indigo-300">Analyze with AI</button>
                    </li>
                  ))}
                </ul>
              )}
              <button onClick={() => fileInputRef.current?.click()} className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold cursor-pointer">
                Upload New Document
              </button>
            </div>
          </div>
        )}

        {/* TAB 4: AI TOOLS */}
        {activeTab === "AI Tools" && (
          <div className="flex-1 overflow-y-auto z-10 my-4 pr-1 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
            <div className="grid grid-cols-2 gap-4">
              {[
                { name: "Web Search RAG", desc: "Fetch web context directly into responses.", icon: Globe, query: "Search web for latest AI news" },
                { name: "Text Summarizer", desc: "Condense long paragraphs into key facts.", icon: FileText, query: "Summarize text:" },
                { name: "OCR Extractor", desc: "Extract structured JSON from documents.", icon: Wrench, query: "Extract order details from invoice" },
                { name: "Code Debugger", desc: "Identify bugs and syntax errors.", icon: Code, query: "Debug python script:" },
              ].map((tool, idx) => (
                <div key={idx} className={`p-5 rounded-2xl border backdrop-blur-md flex flex-col justify-between ${darkMode ? "bg-indigo-950/30 border-indigo-500/20" : "bg-white border-slate-200"}`}>
                  <div>
                    <tool.icon className="h-6 w-6 text-indigo-400 mb-2" />
                    <h4 className="font-bold text-sm">{tool.name}</h4>
                    <p className="text-xs opacity-70 mt-1">{tool.desc}</p>
                  </div>
                  <button onClick={() => { setActiveTab("Chats"); handleSend(tool.query); }} className="mt-4 py-2 px-3 rounded-xl bg-indigo-600/20 border border-indigo-500/30 text-indigo-300 text-xs font-medium hover:bg-indigo-600 hover:text-white transition-colors cursor-pointer">
                    Launch Tool
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* TAB 5: CODE ASSISTANT */}
        {activeTab === "Code Assistant" && (
          <div className="flex-1 overflow-y-auto z-10 my-4 space-y-4 pr-1 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
            <div className={`p-6 rounded-2xl border backdrop-blur-md font-mono ${darkMode ? "bg-[#090d22] border-indigo-500/20" : "bg-slate-900 text-slate-100"}`}>
              <div className="flex items-center gap-2 mb-3 text-xs text-indigo-400">
                <Terminal className="h-4 w-4" /> Code Generation Mode
              </div>
              <p className="text-xs opacity-70 font-sans mb-4">Ask Qwen AI to generate React components, Python scripts, or API integrations.</p>
              <button onClick={() => { setActiveTab("Chats"); handleSend("Write a Python FastAPI script with CORS enabled."); }} className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-sans font-semibold cursor-pointer">
                Generate Sample Code
              </button>
            </div>
          </div>
        )}

        {/* TAB 6: SETTINGS */}
        {activeTab === "Settings" && (
          <div className="flex-1 overflow-y-auto z-10 my-4 space-y-6 pr-1 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
            <div className={`p-6 rounded-2xl border backdrop-blur-md space-y-4 ${darkMode ? "bg-indigo-950/30 border-indigo-500/20" : "bg-white border-slate-200"}`}>
              <h3 className="text-base font-bold flex items-center gap-2">
                <Sliders className="h-4 w-4 text-indigo-400" /> Model Configuration
              </h3>
              <div className="space-y-4 text-xs">
                <div>
                  <label className="block font-semibold mb-1">Temperature ({temperature})</label>
                  <input type="range" min="0" max="1" step="0.1" value={temperature} onChange={(e) => setTemperature(parseFloat(e.target.value))} className="w-full" />
                </div>
                <div>
                  <label className="block font-semibold mb-1">Max Tokens ({maxTokens})</label>
                  <input type="range" min="256" max="2048" step="128" value={maxTokens} onChange={(e) => setMaxTokens(parseInt(e.target.value))} className="w-full" />
                </div>
              </div>
            </div>
          </div>
        )}

        {/* INPUT PROMPT BOX */}
        <div className="z-10 mt-2">
          <div className={`p-2.5 rounded-2xl border backdrop-blur-xl shadow-2xl transition-all ${
            darkMode ? "bg-[#0b0f2a]/90 border-indigo-500/30 focus-within:border-indigo-500" : "bg-white border-slate-300 focus-within:border-indigo-600"
          }`}>
            <textarea
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), handleSend())}
              placeholder="Type your message or ask a question..."
              className={`w-full bg-transparent px-3 py-1.5 text-sm focus:outline-none resize-none ${
                darkMode ? "text-white placeholder-slate-500" : "text-slate-900 placeholder-slate-400"
              }`}
            />
            <div className="flex items-center justify-between px-2 pt-1 border-t border-black/5 dark:border-white/5">
              <div className="flex items-center gap-2 opacity-60">
                <button onClick={() => fileInputRef.current?.click()} className="p-1.5 hover:opacity-100 rounded-lg transition-all cursor-pointer" title="Attach file">
                  <Paperclip className="h-4 w-4" />
                </button>
              </div>
              <button onClick={() => handleSend()} disabled={isLoading} className="p-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white shadow-md shadow-indigo-500/30 transition-all cursor-pointer disabled:opacity-50">
                <Send className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      </main>

      {/* RIGHT SIDEBAR */}
      <aside className={`w-80 border-l p-5 flex flex-col gap-5 backdrop-blur-xl overflow-y-auto z-20 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden transition-colors duration-300 ${
        darkMode ? "border-indigo-900/30 bg-[#080b1e]/95" : "border-slate-300 bg-white/80"
      }`}>
        <div className="p-5 rounded-2xl bg-gradient-to-br from-indigo-950/80 to-purple-950/60 border border-indigo-500/30 flex flex-col items-center justify-center relative overflow-hidden shadow-xl">
          <div className="w-16 h-16 rounded-2xl bg-gradient-to-tr from-indigo-600 to-purple-600 flex items-center justify-center shadow-lg shadow-purple-500/30 mb-2 relative z-10">
            <QwenLogo size="w-10 h-10" />
          </div>
        </div>

        <div className={`p-4 rounded-xl border space-y-3 ${darkMode ? "bg-[#0e1338]/80 border-indigo-500/20" : "bg-slate-100 border-slate-200"}`}>
          <h3 className="text-xs font-semibold tracking-wider opacity-80">Capabilities</h3>
          <div className="space-y-2 text-xs opacity-90">
            {[
              { label: "Natural Conversations", icon: MessageSquare },
              { label: "Document Q&A (RAG)", icon: FileText },
              { label: "Code Generation", icon: Code },
              { label: "Reasoning & Analysis", icon: Activity },
              { label: "Web Search", icon: Globe },
            ].map((item, idx) => (
              <div key={idx} className="flex items-center gap-2.5">
                <item.icon className="h-3.5 w-3.5 text-indigo-400" />
                <span>{item.label}</span>
              </div>
            ))}
          </div>
        </div>

        <div className={`p-4 rounded-xl border space-y-3 ${darkMode ? "bg-[#0e1338]/80 border-indigo-500/20" : "bg-slate-100 border-slate-200"}`}>
          <h3 className="text-xs font-semibold tracking-wider opacity-80">Upload Document</h3>
          <div
            onClick={() => fileInputRef.current?.click()}
            className="border-2 border-dashed border-indigo-500/30 rounded-xl p-4 text-center hover:border-indigo-400 cursor-pointer transition-colors bg-indigo-950/10"
          >
            <Upload className="h-5 w-5 text-indigo-400 mx-auto mb-1.5" />
            <div className="text-xs font-medium">
              {isUploading ? "Uploading..." : "Drag & drop your file here"}
            </div>
            <div className="text-[10px] opacity-70 mt-1">or <span className="text-indigo-400 underline">click to browse</span></div>
            <div className="text-[9px] opacity-50 mt-1">Supports PDF, PNG, TXT, DOCX</div>
          </div>
        </div>

        <div className={`p-4 rounded-xl border space-y-3 ${darkMode ? "bg-[#0e1338]/80 border-indigo-500/20" : "bg-slate-100 border-slate-200"}`}>
          <h3 className="text-xs font-semibold tracking-wider opacity-80">Chat Stats</h3>
          <div className="space-y-2 text-xs">
            <div className="flex justify-between opacity-80">
              <span className="flex items-center gap-1.5"><MessageSquare className="h-3.5 w-3.5 text-indigo-400" /> Messages</span>
              <span className="font-semibold">{messages.length}</span>
            </div>
            <div className="flex justify-between opacity-80">
              <span className="flex items-center gap-1.5"><Zap className="h-3.5 w-3.5 text-indigo-400" /> Last Latency</span>
              <span className="font-semibold text-emerald-500">{lastLatency !== null ? `${lastLatency}s` : "N/A"}</span>
            </div>
            <div className="flex justify-between opacity-80">
              <span className="flex items-center gap-1.5"><AlertTriangle className={`h-3.5 w-3.5 ${failureCount > 0 ? "text-rose-400" : "text-indigo-400"}`} /> Total Failures</span>
              <span className={`font-semibold ${failureCount > 0 ? "text-rose-400" : ""}`}>{failureCount}</span>
            </div>
            <div className="flex justify-between opacity-80">
              <span className="flex items-center gap-1.5"><Cpu className="h-3.5 w-3.5 text-indigo-400" /> Model</span>
              <span className="font-semibold">Qwen 2.5 - 1.5B</span>
            </div>
          </div>
        </div>
      </aside>
    </div>
  );
}