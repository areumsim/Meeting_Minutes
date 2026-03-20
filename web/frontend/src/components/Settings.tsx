import React, { useState, useEffect } from "react";
import {
  Settings, Plus, Trash2, CheckCircle, Save, KeyRound, Mic, FileText, Loader2
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import {
  getConfig, updateConfig, getProfiles, createProfile, deleteProfile, clearSessions,
  getApiKey, setApiKey, getAnthropicKey, setAnthropicKey,
  getTargetEmail, setTargetEmail
} from "../lib/api";
import type { Profile } from "../lib/types";

export default function SettingsView() {
  const [config, setConfig] = useState<any>(null);
  
  // API Keys & Email
  const [openaiKey, setOpenaiKey] = useState("");
  const [claudeKey, setClaudeKey] = useState("");
  const [email, setEmail] = useState("");
  
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  
  const [showNewProfile, setShowNewProfile] = useState(false);
  const [newProfile, setNewProfile] = useState({ name: "", description: "", type: "meeting", language: "ko", translate: false });

  const load = async () => {
    setOpenaiKey(getApiKey());
    setClaudeKey(getAnthropicKey());
    setEmail(getTargetEmail());
    const [cfg, profs] = await Promise.all([getConfig(), getProfiles()]);
    setConfig(cfg);
    setProfiles(profs);
  };

  useEffect(() => { load(); }, []);

  const handleSaveAll = async () => {
    if (!config) return;
    setSaving(true);
    try {
      // Save Keys & Email
      setApiKey(openaiKey);
      setAnthropicKey(claudeKey);
      setTargetEmail(email);
      
      // Save Config
      await updateConfig(config);
      
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      console.error(e);
    }
    setSaving(false);
  };

  const handleCreateProfile = async () => {
    if (!newProfile.name.trim()) return;
    await createProfile(newProfile);
    setShowNewProfile(false);
    setNewProfile({ name: "", description: "", type: "meeting", language: "ko", translate: false });
    const profs = await getProfiles();
    setProfiles(profs);
  };

  const handleDeleteProfile = async (name: string) => {
    if (!confirm(`Delete profile "${name}"?`)) return;
    await deleteProfile(name);
    const profs = await getProfiles();
    setProfiles(profs);
  };

  const handleClearHistory = async () => {
    if (!confirm("Delete ALL session history from this device? This cannot be undone.")) return;
    await clearSessions();
    alert("History cleared successfully.");
  };

  const updateConfigField = (section: string, key: string, value: any) => {
    setConfig((prev: any) => ({
      ...prev,
      [section]: { ...(prev?.[section] || {}), [key]: value },
    }));
  };

  if (!config) return null;

  return (
    <div className="max-w-3xl mx-auto px-1 md:px-0">
      <h2 className="text-3xl font-bold tracking-tight mb-2">Settings</h2>
      <p className="text-brand-500 mb-8">Configure your Local AI App environment.</p>

      {/* API Keys (Most Important for Serverless) */}
      <section className="bg-white border border-brand-200 rounded-2xl p-6 md:p-8 mb-6 shadow-sm">
        <h3 className="text-lg font-bold mb-4 flex items-center gap-2 text-brand-900">
          <KeyRound size={18} /> API Keys
        </h3>
        <p className="text-sm text-brand-500 mb-6">
          Your API keys are stored <strong className="text-emerald-600">securely on this device only</strong>. They are never sent to any intermediary server.
        </p>
        
        <div className="space-y-5">
          <div className="space-y-2">
            <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest flex items-center gap-2">
              <Mic size={14} /> OpenAI API Key (Required for STT/Realtime)
            </label>
            <input
              type="password"
              value={openaiKey}
              onChange={(e) => setOpenaiKey(e.target.value)}
              placeholder="sk-proj-..."
              className="w-full px-4 py-3 bg-zinc-50 border border-zinc-200 rounded-xl outline-none focus:ring-2 focus:ring-brand-500 font-mono text-sm tracking-widest"
            />
          </div>
          
          <div className="space-y-2">
            <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest flex items-center gap-2">
              <FileText size={14} /> Anthropic API Key (Optional for Claude Summaries)
            </label>
            <input
              type="password"
              value={claudeKey}
              onChange={(e) => setClaudeKey(e.target.value)}
              placeholder="sk-ant-..."
              className="w-full px-4 py-3 bg-zinc-50 border border-zinc-200 rounded-xl outline-none focus:ring-2 focus:ring-brand-500 font-mono text-sm tracking-widest"
            />
          </div>

          <div className="space-y-2">
            <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest flex items-center gap-2">
              Target Email Address (For quick sharing)
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="team@company.com"
              className="w-full px-4 py-3 bg-zinc-50 border border-zinc-200 rounded-xl outline-none focus:ring-2 focus:ring-brand-500 text-sm"
            />
          </div>
        </div>
      </section>

      {/* Model Settings */}
      <section className="bg-white border border-brand-200 rounded-2xl p-6 md:p-8 mb-6 shadow-sm">
        <h3 className="text-lg font-bold mb-6 flex items-center gap-2">
          <Settings size={18} /> Model Configuration
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="space-y-2">
            <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">STT Model</label>
            <input
              type="text"
              value={config.models.stt}
              onChange={(e) => updateConfigField("models", "stt", e.target.value)}
              className="w-full px-4 py-3 bg-zinc-50 border border-zinc-200 rounded-xl outline-none focus:ring-2 focus:ring-zinc-900 font-mono text-sm"
            />
          </div>
          <div className="space-y-2">
            <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">GPT Model</label>
            <input
              type="text"
              value={config.models.gpt_model}
              onChange={(e) => updateConfigField("models", "gpt_model", e.target.value)}
              className="w-full px-4 py-3 bg-zinc-50 border border-zinc-200 rounded-xl outline-none focus:ring-2 focus:ring-zinc-900 font-mono text-sm"
            />
          </div>
          <div className="space-y-2">
            <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">Claude Model</label>
            <input
              type="text"
              value={config.models.claude_model}
              onChange={(e) => updateConfigField("models", "claude_model", e.target.value)}
              className="w-full px-4 py-3 bg-zinc-50 border border-zinc-200 rounded-xl outline-none focus:ring-2 focus:ring-zinc-900 font-mono text-sm"
            />
          </div>
          <div className="space-y-2">
            <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">Translate Model</label>
            <input
              type="text"
              value={config.models.translate_model}
              onChange={(e) => updateConfigField("models", "translate_model", e.target.value)}
              className="w-full px-4 py-3 bg-zinc-50 border border-zinc-200 rounded-xl outline-none focus:ring-2 focus:ring-zinc-900 font-mono text-sm"
            />
          </div>
        </div>

        <button
          onClick={handleSaveAll}
          disabled={saving}
          className="mt-8 w-full md:w-auto flex items-center justify-center gap-2 px-8 py-3.5 bg-brand-950 text-white rounded-xl font-bold hover:bg-brand-900 transition-all shadow-xl active:scale-95"
        >
          {saving ? <Loader2 size={18} className="animate-spin" /> : saved ? <CheckCircle size={18} /> : <Save size={18} />}
          {saved ? "All Settings Saved!" : "Save All Settings"}
        </button>
      </section>

      {/* Profiles */}
      <section className="bg-white border border-brand-200 rounded-2xl p-6 md:p-8 mb-6">
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-bold">Processing Profiles</h3>
          <button
            onClick={() => setShowNewProfile(!showNewProfile)}
            className="flex items-center gap-2 px-4 py-2 bg-brand-50 text-brand-700 rounded-xl text-sm font-medium hover:bg-brand-100 transition-all"
          >
            <Plus size={14} /> New Profile
          </button>
        </div>

        <AnimatePresence>
          {showNewProfile && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden mb-6"
            >
              <div className="p-6 bg-zinc-50 rounded-xl space-y-4 border border-zinc-200">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <input
                    type="text"
                    value={newProfile.name}
                    onChange={(e) => setNewProfile(p => ({ ...p, name: e.target.value }))}
                    placeholder="Profile name (e.g. weekly_team)"
                    className="px-4 py-2 border border-zinc-200 rounded-lg outline-none focus:ring-2 focus:ring-zinc-900 text-sm"
                  />
                  <input
                    type="text"
                    value={newProfile.description}
                    onChange={(e) => setNewProfile(p => ({ ...p, description: e.target.value }))}
                    placeholder="Description"
                    className="px-4 py-2 border border-zinc-200 rounded-lg outline-none focus:ring-2 focus:ring-zinc-900 text-sm"
                  />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <select
                    value={newProfile.type}
                    onChange={(e) => setNewProfile(p => ({ ...p, type: e.target.value }))}
                    className="px-3 py-2 border border-zinc-200 rounded-lg text-sm bg-white"
                  >
                    <option value="meeting">Meeting</option>
                    <option value="seminar">Seminar</option>
                    <option value="lecture">Lecture</option>
                  </select>
                  <select
                    value={newProfile.language}
                    onChange={(e) => setNewProfile(p => ({ ...p, language: e.target.value }))}
                    className="px-3 py-2 border border-zinc-200 rounded-lg text-sm bg-white"
                  >
                    <option value="ko">Korean</option>
                    <option value="en">English</option>
                  </select>
                  <label className="flex items-center gap-2 text-sm ml-2">
                    <input
                      type="checkbox"
                      checked={newProfile.translate}
                      onChange={(e) => setNewProfile(p => ({ ...p, translate: e.target.checked }))}
                      className="w-4 h-4 rounded border-brand-300 text-brand-900 focus:ring-brand-900"
                    />
                    Translate EN → KO
                  </label>
                </div>
                <button
                  onClick={handleCreateProfile}
                  className="w-full md:w-auto px-6 py-2.5 bg-brand-950 text-white rounded-lg text-sm font-semibold hover:bg-brand-900 transition-all mt-2"
                >
                  Create Profile
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="space-y-3">
          {profiles.map(p => (
            <div key={p.name} className="flex flex-col md:flex-row md:items-center justify-between p-4 bg-zinc-50 border border-zinc-100 rounded-xl gap-3">
              <div>
                <span className="font-bold text-sm text-zinc-900">{p.name}</span>
                <span className="text-xs text-zinc-500 ml-3">{p.description}</span>
                <span className="text-[10px] text-brand-500 font-bold ml-2 uppercase bg-brand-50 px-2 py-0.5 rounded-md">[{p.source}]</span>
              </div>
              <div className="flex items-center gap-3 text-xs text-zinc-500 font-medium">
                <span className="bg-white px-2 py-1 rounded shadow-sm">{p.type}</span>
                <span className="bg-white px-2 py-1 rounded shadow-sm">{p.language}</span>
                {p.translate && <span className="bg-amber-50 text-amber-700 px-2 py-1 rounded shadow-sm">Translating</span>}
                {p.source !== "builtin" && (
                  <button onClick={() => handleDeleteProfile(p.name)} className="p-1.5 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors ml-1">
                    <Trash2 size={16} />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Danger Zone */}
      <section className="bg-white border border-red-200 rounded-2xl p-6 md:p-8">
        <h3 className="text-lg font-bold text-red-600 mb-2">Danger Zone</h3>
        <p className="text-sm text-red-500/80 mb-5">This action will delete all locally stored sessions, transcripts, and summaries from this device.</p>
        <button
          onClick={handleClearHistory}
          className="flex items-center justify-center w-full md:w-auto gap-2 px-6 py-3 bg-red-50 text-red-600 border border-red-200 rounded-xl font-bold hover:bg-red-100 transition-all"
        >
          <Trash2 size={16} /> Delete All Device History
        </button>
      </section>
    </div>
  );
}
