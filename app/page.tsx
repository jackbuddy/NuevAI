'use client';

import React, { useState, useRef, useEffect } from 'react';
import { 
  Search, 
  Send, 
  Calendar as CalendarIcon, 
  BookOpen, 
  MessageSquare, 
  Sparkles, 
  ChevronDown, 
  ChevronUp, 
  ShieldCheck, 
  Wifi, 
  UserCheck, 
  Trophy 
} from 'lucide-react';

interface Source {
  chunk_id?: string;
  similarity?: number;
  title?: string;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  sources?: Source[];
}

export default function Home() {
  const [activeTab, setActiveTab] = useState<'calendar' | 'chat' | 'resources'>('chat');
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [expandedSources, setExpandedSources] = useState<Record<number, boolean>>({});

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const starterPrompts = [
    "What sports are playing this season?",
    "What engineering courses are offered this semester?",
    "What is the cell phone policy in the handbook?"
  ];

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  const handleSend = async (queryText?: string) => {
    const textToSend = queryText || input;
    if (!textToSend.trim() || isLoading) return;

    const userMessage: Message = { role: 'user', content: textToSend };
    setMessages((prev) => [...prev, userMessage]);
    if (!queryText) setInput('');
    setIsLoading(true);

    try {
      const response = await fetch('http://localhost:8000/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: textToSend }),
      });

      if (!response.ok) throw new Error('Failed to connect to backend server');

      const data = await response.json();
      const assistantMessage: Message = {
        role: 'assistant',
        content: data.answer || 'No response returned from the server.',
        sources: data.sources || [],
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: 'Unable to connect to NuevAI service. Ensure the FastAPI backend is running on port 8000.',
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const toggleSources = (index: number) => {
    setExpandedSources((prev) => ({ ...prev, [index]: !prev[index] }));
  };

  return (
    <div className="min-h-screen bg-[#F8FAFC] text-[#0F172A] flex flex-col font-sans selection:bg-[#E0F2FE]">
      {/* Top Header & Segmented Switcher */}
      <header className="sticky top-0 z-50 bg-white/80 backdrop-blur-md border-b border-slate-200/60 py-3 px-6 flex items-center justify-between shadow-xs">
        {/* Status Badge */}
        <div className="flex items-center gap-2.5">
          <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
          <span className="font-bold text-[#08519C] text-sm tracking-tight">NuevAI Assistant</span>
        </div>

        {/* Segmented Controller */}
        <div className="bg-slate-100 p-1 rounded-full border border-slate-200 shadow-inner flex items-center gap-1">
          <button
            onClick={() => setActiveTab('calendar')}
            className={`px-4 py-1.5 rounded-full text-xs font-semibold transition-all flex items-center gap-1.5 ${
              activeTab === 'calendar'
                ? 'bg-white text-[#08519C] shadow-sm'
                : 'text-slate-500 hover:text-slate-800'
            }`}
          >
            <CalendarIcon className="w-3.5 h-3.5" />
            Calendar
          </button>
          <button
            onClick={() => setActiveTab('chat')}
            className={`px-4 py-1.5 rounded-full text-xs font-semibold transition-all flex items-center gap-1.5 ${
              activeTab === 'chat'
                ? 'bg-white text-[#08519C] shadow-sm'
                : 'text-slate-500 hover:text-slate-800'
            }`}
          >
            <MessageSquare className="w-3.5 h-3.5" />
            Chat
          </button>
          <button
            onClick={() => setActiveTab('resources')}
            className={`px-4 py-1.5 rounded-full text-xs font-semibold transition-all flex items-center gap-1.5 ${
              activeTab === 'resources'
                ? 'bg-white text-[#08519C] shadow-sm'
                : 'text-slate-500 hover:text-slate-800'
            }`}
          >
            <BookOpen className="w-3.5 h-3.5" />
            Resources
          </button>
        </div>

        <div className="w-24 hidden sm:block" />
      </header>

      {/* Main Container */}
      <main className="flex-1 flex flex-col max-w-4xl w-full mx-auto p-4 md:p-6">
        {/* TAB 1: CHAT VIEW */}
        {activeTab === 'chat' && (
          <div className="flex-1 flex flex-col justify-between">
            {/* Hero State when no messages */}
            {messages.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center my-auto text-center py-12">
                <div className="w-12 h-12 rounded-2xl bg-[#E0F2FE] flex items-center justify-center text-[#3182BD] mb-4 shadow-xs">
                  <Sparkles className="w-6 h-6" />
                </div>
                <h1 className="text-3xl md:text-4xl font-bold text-[#08519C] tracking-tight mb-2">
                  How can NuevAI help you today?
                </h1>
                <p className="text-slate-500 max-w-md mb-8 text-sm md:text-base">
                  Ask about Upper School courses, athletics schedules, handbooks, and campus policies.
                </p>

                {/* Google-Style Search Bar */}
                <div className="w-full max-w-xl mb-6">
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      handleSend();
                    }}
                    className="relative flex items-center"
                  >
                    <Search className="absolute left-5 w-5 h-5 text-[#3182BD]" />
                    <input
                      type="text"
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      placeholder="Ask NuevAI a question..."
                      className="w-full bg-white rounded-full border-2 border-slate-200 shadow-md pl-13 pr-14 py-4 text-sm md:text-base text-slate-800 placeholder-slate-400 hover:border-[#6BAED6] focus:border-[#3182BD] focus:outline-hidden focus:ring-4 focus:ring-[#3182BD]/10 transition-all"
                    />
                    <button
                      type="submit"
                      disabled={!input.trim() || isLoading}
                      className="absolute right-3 p-2.5 rounded-full bg-[#3182BD] hover:bg-[#08519C] text-white disabled:opacity-30 transition-all shadow-xs"
                    >
                      <Send className="w-4 h-4" />
                    </button>
                  </form>
                </div>

                {/* Starter Prompt Chips */}
                <div className="flex flex-wrap justify-center gap-2 max-w-xl">
                  {starterPrompts.map((prompt, idx) => (
                    <button
                      key={idx}
                      onClick={() => handleSend(prompt)}
                      className="bg-[#E0F2FE] hover:bg-[#3182BD] text-[#08519C] hover:text-white px-4 py-2 rounded-full text-xs font-medium border border-[#6BAED6]/30 transition-all cursor-pointer shadow-2xs"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              /* Active Conversation Feed */
              <div className="flex-1 overflow-y-auto space-y-4 pb-28 pt-2">
                {messages.map((msg, idx) => (
                  <div
                    key={idx}
                    className={`flex flex-col ${
                      msg.role === 'user' ? 'items-end' : 'items-start'
                    }`}
                  >
                    <div
                      className={`max-w-[85%] px-5 py-3.5 rounded-2xl text-sm leading-relaxed ${
                        msg.role === 'user'
                          ? 'bg-[#E0F2FE] text-[#08519C] border border-[#6BAED6]/30 rounded-tr-xs shadow-2xs'
                          : 'bg-white text-slate-800 border border-slate-200 rounded-tl-xs shadow-2xs'
                      }`}
                    >
                      {msg.content}
                    </div>

                    {/* Source Attribution (Privacy Preserving) */}
                    {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
                      <div className="mt-1.5 ml-1">
                        <button
                          onClick={() => toggleSources(idx)}
                          className="flex items-center gap-1 text-[11px] font-medium text-slate-400 hover:text-[#3182BD] transition-colors"
                        >
                          <span>{msg.sources.length} sources referenced</span>
                          {expandedSources[idx] ? (
                            <ChevronUp className="w-3 h-3" />
                          ) : (
                            <ChevronDown className="w-3 h-3" />
                          )}
                        </button>
                        {expandedSources[idx] && (
                          <div className="mt-1 p-2 bg-slate-100 rounded-lg text-[11px] text-slate-600 border border-slate-200">
                            Upper School Official Documentation & Handbooks
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}

                {/* Loading State */}
                {isLoading && (
                  <div className="flex items-start">
                    <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-xs px-5 py-3.5 shadow-2xs flex items-center gap-1.5">
                      <div className="w-2 h-2 rounded-full bg-[#3182BD] animate-bounce" />
                      <div className="w-2 h-2 rounded-full bg-[#3182BD] animate-bounce [animation-delay:0.2s]" />
                      <div className="w-2 h-2 rounded-full bg-[#3182BD] animate-bounce [animation-delay:0.4s]" />
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>
            )}

            {/* Bottom Input Field for Active Chat */}
            {messages.length > 0 && (
              <div className="fixed bottom-0 left-0 right-0 bg-white/80 backdrop-blur-md border-t border-slate-200/80 p-4 z-40">
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    handleSend();
                  }}
                  className="max-w-4xl mx-auto relative flex items-center"
                >
                  <input
                    type="text"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="Ask NuevAI a follow-up question..."
                    className="w-full bg-white rounded-full border-2 border-slate-200 shadow-sm pl-6 pr-14 py-3.5 text-sm text-slate-800 placeholder-slate-400 hover:border-[#6BAED6] focus:border-[#3182BD] focus:outline-hidden focus:ring-4 focus:ring-[#3182BD]/10 transition-all"
                  />
                  <button
                    type="submit"
                    disabled={!input.trim() || isLoading}
                    className="absolute right-3 p-2 rounded-full bg-[#3182BD] hover:bg-[#08519C] text-white disabled:opacity-30 transition-all"
                  >
                    <Send className="w-4 h-4" />
                  </button>
                </form>
              </div>
            )}
          </div>
        )}

        {/* TAB 2: RESOURCES VIEW */}
        {activeTab === 'resources' && (
          <div className="py-6 space-y-6">
            <div className="text-center mb-8">
              <h2 className="text-2xl font-bold text-[#08519C]">Campus Resources</h2>
              <p className="text-slate-500 text-sm">Essential guides, tools, and contacts for Nueva Upper School</p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-2xs hover:border-[#6BAED6] transition-all">
                <div className="w-10 h-10 rounded-xl bg-[#E0F2FE] flex items-center justify-center text-[#08519C] mb-3">
                  <ShieldCheck className="w-5 h-5" />
                </div>
                <h3 className="font-bold text-slate-800 mb-1">Essentials & Handbooks</h3>
                <p className="text-xs text-slate-500 leading-relaxed">
                  Student/Parent Handbooks, Honor Code policies, attendance procedures, and cell phone/tech guidelines.
                </p>
              </div>

              <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-2xs hover:border-[#6BAED6] transition-all">
                <div className="w-10 h-10 rounded-xl bg-[#E0F2FE] flex items-center justify-center text-[#08519C] mb-3">
                  <UserCheck className="w-5 h-5" />
                </div>
                <h3 className="font-bold text-slate-800 mb-1">People & Places</h3>
                <p className="text-xs text-slate-500 leading-relaxed">
                  Directory guidance for Upper School Leadership, Deans, the Writing & Research Center (WRC), and Science Labs.
                </p>
              </div>

              <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-2xs hover:border-[#6BAED6] transition-all">
                <div className="w-10 h-10 rounded-xl bg-[#E0F2FE] flex items-center justify-center text-[#08519C] mb-3">
                  <Wifi className="w-5 h-5" />
                </div>
                <h3 className="font-bold text-slate-800 mb-1">IT & Campus Tools</h3>
                <p className="text-xs text-slate-500 leading-relaxed">
                  Instructions for Nueva Wi-Fi setup, PaperCut printer installations, and school portal access.
                </p>
              </div>

              <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-2xs hover:border-[#6BAED6] transition-all">
                <div className="w-10 h-10 rounded-xl bg-[#E0F2FE] flex items-center justify-center text-[#08519C] mb-3">
                  <Sparkles className="w-5 h-5" />
                </div>
                <h3 className="font-bold text-slate-800 mb-1">About NuevAI</h3>
                <p className="text-xs text-slate-500 leading-relaxed">
                  High-utility assistant dedicated to helping the Nueva community navigate schedules, courses, and school life.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* TAB 3: CALENDAR VIEW */}
        {activeTab === 'calendar' && (
          <div className="py-6 space-y-6">
            <div className="text-center mb-8">
              <h2 className="text-2xl font-bold text-[#08519C]">Schedules & Calendar</h2>
              <p className="text-slate-500 text-sm">Key academic dates, athletics events, and club schedules</p>
            </div>
            <div className="space-y-4">
              <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-2xs">
                <div className="flex items-center gap-2 mb-2 text-[#08519C] font-semibold text-sm">
                  <Trophy className="w-4 h-4 text-[#3182BD]" />
                  Athletics & Sports Schedule
                </div>
                <p className="text-xs text-slate-500">
                  Track varsity game days, home vs. away locations, practice schedules, and athletic department notices.
                </p>
              </div>

              <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-2xs">
                <div className="flex items-center gap-2 mb-2 text-[#08519C] font-semibold text-sm">
                  <CalendarIcon className="w-4 h-4 text-[#3182BD]" />
                  Academic & Key Dates
                </div>
                <p className="text-xs text-slate-500">
                  Trimester start/end dates, parent-teacher conferences, late-start Wednesdays, and holiday breaks.
                </p>
              </div>

              <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-2xs">
                <div className="flex items-center gap-2 mb-2 text-[#08519C] font-semibold text-sm">
                  <BookOpen className="w-4 h-4 text-[#3182BD]" />
                  Student Life & Clubs
                </div>
                <p className="text-xs text-slate-500">
                  Assembly dates, Spirit Weeks, Friday Club Leader meetings, and advisory gatherings.
                </p>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
