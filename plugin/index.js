import { spawn } from "node:child_process";
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";
const Type = {
  String: (options = {}) => ({ type: "string", ...options }),
  Optional: (schema) => ({ ...schema, optional: true }),
  Object: (properties = {}, options = {}) => {
    const required = Object.entries(properties)
      .filter(([, schema]) => !schema?.optional)
      .map(([key]) => key);
    const cleanProperties = Object.fromEntries(
      Object.entries(properties).map(([key, schema]) => {
        const { optional, ...rest } = schema || {};
        return [key, rest];
      }),
    );
    return {
      type: "object",
      properties: cleanProperties,
      ...(required.length ? { required } : {}),
      ...options,
    };
  },
};

const DEFAULT_MANAGER_CANDIDATES = [
  process.env.GPTPROF_MANAGER_PATH,
  process.env.CODEX_PROFILE_MANAGER,
  `${process.env.HOME || ""}/.local/bin/codex-profile-manager.py`,
  "/usr/local/bin/codex-profile-manager.py",
].filter(Boolean);
const DEFAULT_PYTHON = process.env.GPTPROF_PYTHON || "python3";
const DEFAULT_TIMEOUT_MS = 12_000;

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function getConfig(api) {
  const config = asObject(api?.config);
  const managerPath = typeof config.managerPath === "string" && config.managerPath.trim()
    ? config.managerPath.trim()
    : DEFAULT_MANAGER_CANDIDATES[0];
  return {
    enabled: config.enabled !== false,
    managerPath,
    pythonCommand: typeof config.pythonCommand === "string" && config.pythonCommand.trim() ? config.pythonCommand.trim() : DEFAULT_PYTHON,
    timeoutMs: Number.isFinite(config.timeoutMs) && config.timeoutMs > 0 ? Math.min(Number(config.timeoutMs), 120_000) : DEFAULT_TIMEOUT_MS,
    restartAfterSwitch: config.restartAfterSwitch !== false,
  };
}

function textResult(text, details, channelData) {
  return {
    content: [{ type: "text", text }],
    details,
    ...(channelData ? { channelData } : {}),
  };
}

function runManager(config, args) {
  return new Promise((resolve) => {
    const child = spawn(config.pythonCommand, [config.managerPath, ...args], { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const settle = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ ...result, stdout, stderr });
    };
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      settle({ code: null, timedOut: true });
    }, config.timeoutMs);
    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
      if (stdout.length > 256_000) stdout = stdout.slice(-256_000);
    });
    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
      if (stderr.length > 32_000) stderr = stderr.slice(-32_000);
    });
    child.on("error", (error) => {
      stderr += String(error);
      settle({ code: null, timedOut: false });
    });
    child.on("close", (code) => settle({ code, timedOut: false }));
  });
}

function parseJson(text) {
  try {
    const parsed = JSON.parse(text);
    return asObject(parsed);
  } catch {
    return {};
  }
}

function firstString(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function eventText(event) {
  return firstString(
    event?.messageText,
    event?.text,
    event?.content,
    event?.bodyForAgent,
    event?.body,
    event?.input,
    event?.prompt,
    event?.message?.text,
    event?.message?.message,
    event?.message?.content,
    event?.update?.message?.text,
    event?.update?.message?.message,
    event?.telegram?.message?.text,
    event?.telegram?.message?.message,
    event?.ctx?.message?.text,
    event?.raw?.message?.text,
  );
}

async function managerJson(config, args) {
  const result = await runManager(config, args);
  const payload = parseJson(result.stdout);
  if (result.code !== 0 || result.timedOut || payload.ok === false) {
    const reason = payload.error || (result.timedOut ? "manager timed out" : result.stderr || "manager failed");
    return { ok: false, error: String(reason).slice(0, 800), payload };
  }
  return payload;
}

function profileUsageMax(status, slug) {
  const entry = asObject(asObject(status.usage)[slug]);
  const usage = asObject(entry.usage);
  const windows = asObject(usage.windows);
  const values = [
    asObject(windows.fiveHour || windows.primary).usedPercent,
    asObject(windows.weekly || windows.secondary).usedPercent,
    usage.primaryUsed,
    usage.secondaryUsed,
  ].map(Number).filter(Number.isFinite);
  return values.length ? Math.max(...values) : null;
}

function profileUsageOverThreshold(status, slug) {
  const maxUsage = profileUsageMax(status, slug);
  return Number.isFinite(maxUsage) && maxUsage >= 95;
}

function profileUsageLeft(status, slug) {
  const maxUsage = profileUsageMax(status, slug);
  if (!Number.isFinite(maxUsage)) return null;
  return Math.max(0, 100 - Math.round(maxUsage));
}

function profilePlanType(status, profile) {
  const fromProfile = typeof profile?.planType === "string" ? profile.planType.trim().toLowerCase() : "";
  if (fromProfile) return fromProfile;
  const entry = asObject(asObject(status.usage)[profile?.slug]);
  const fromUsage = typeof asObject(entry.usage).planType === "string" ? asObject(entry.usage).planType.trim().toLowerCase() : "";
  return fromUsage;
}

function profilePlanBadge(status, profile) {
  const planType = profilePlanType(status, profile);
  if (planType === "plus") return "[$20]";
  if (planType === "prolite") return "[$100]";
  if (planType === "pro") return "[$200]";
  return "";
}

function profileButtons(status) {
  const profiles = Array.isArray(status.profiles) ? status.profiles : [];
  const button = (text, callback_data) => ({ text, callback_data });
  const buttons = profiles.map((profile) => {
    const left = profileUsageLeft(status, profile.slug);
    const overLimit = profileUsageOverThreshold(status, profile.slug);
    const marker = profile.active ? "✓" : overLimit ? "⚠" : "↔";
    const planBadge = profilePlanBadge(status, profile);
    const usagePrefix = Number.isFinite(left) ? ` ${left}%` : "";
    return {
      text: `${marker}${usagePrefix} ${profile.slug}${planBadge ? ` ${planBadge}` : ""}`,
      callback_data: `gptprof:${profile.slug}`,
    };
  });
  buttons.push(button("🔄 Usage", "gptprof:refresh"));
  buttons.push(button("🔁 Autoswitch", "gptprof:autoswitch"));
  buttons.push(button("➕ Add", "gptprof:device-start"));
  buttons.push(button("✅ Check auth", "gptprof:device-check"));
  if (status?.route?.nativeCodexRoute) {
    buttons.push(button("↩️ Back to Pi route", "gptprof:route-pi"));
  } else {
    buttons.push(button("🧪 Try Codex runtime", "gptprof:route-native"));
  }
  if (!status?.route?.ok && !status?.route?.nativeCodexRoute) {
    buttons.push(button("🛠 Fix Pi route", "gptprof:route-pi"));
  }
  const rows = [];
  for (let index = 0; index < buttons.length; index += 2) {
    rows.push(buttons.slice(index, index + 2));
  }
  return rows;
}

function formatPercent(value) {
  if (!Number.isFinite(Number(value))) return "?";
  return `${Math.round(Number(value))}%`;
}

function formatLeft(value) {
  if (!Number.isFinite(Number(value))) return "?";
  return `${Math.max(0, Math.round(100 - Number(value)))}% left`;
}

function windowLeftLine(icon, label, window, countdownValue) {
  const used = Number(window?.usedPercent);
  if (!Number.isFinite(used)) return `${icon} ${label}: usage unavailable`;
  const left = Math.max(0, Math.round(100 - used));
  if (left === 0 && countdownValue) return `${icon} ${label}: 0% left ⏱ ${countdownValue}`;
  return `${icon} ${label}: ${left}% left`;
}

function formatCacheAge(seconds) {
  if (!Number.isFinite(Number(seconds))) return "not checked";
  const value = Number(seconds);
  if (value < 60) return "just now";
  const minutes = Math.round(value / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.round(minutes / 60)}h ago`;
}

function usageLine(status, slug) {
  const entry = asObject(asObject(status.usage)[slug]);
  const usage = asObject(entry.usage);
  const windows = asObject(usage.windows);
  const fiveHour = asObject(windows.fiveHour || windows.primary);
  const weekly = asObject(windows.weekly || windows.secondary);
  const countdown = asObject(entry.countdown);
  if (!entry.usage && !entry.lastError) return "📊 Usage: not checked yet";
  const stale = entry.fresh === false ? " · stale" : "";
  const error = entry.lastError?.error ? ` · last error: ${entry.lastError.error}` : "";
  if (!entry.usage) return `📊 Usage: unavailable${error}`;
  const fiveHourLine = windowLeftLine("📊", "5h", fiveHour, countdown.fiveHour);
  const weekLine = windowLeftLine("📅", "Week", weekly, countdown.weekly);
  return [
    fiveHourLine,
    weekLine,
    `🕒 Cache: ${formatCacheAge(entry.ageSeconds)}${stale}${error}`,
  ].join("\n");
}

function autoswitchLine(result) {
  if (!result || typeof result !== "object") return "";
  if (result.ok === false) return `⚠️ Autoswitch skipped: ${result.reason || result.error || "usage unavailable"}`;
  if (result.switched) return `🔁 Autoswitch: ${result.from} → ${result.to}`;
  if (result.reason === "below_threshold") return `✅ Autoswitch: no switch needed`;
  if (result.reason === "no_healthy_candidate") return `⚠️ Autoswitch: no spare profile below ${result.threshold || 95}%`;
  if (result.reason) return `ℹ️ Autoswitch: ${result.reason}`;
  return "✅ Autoswitch: no switch needed";
}

function statusText(status) {
  const profiles = Array.isArray(status.profiles) ? status.profiles : [];
  const route = asObject(status.route);
  const runtimeId = route.agentRuntime?.id || "pi";
  const routeLine = route.legacyPiRoute
    ? `✅ Route: OpenAI-Codex OAuth · Pi\n🧠 Model: ${route.primaryModel}\n🧪 Trial available: openai/gpt-5.5 · runtime=codex`
    : route.nativeCodexRoute
      ? `🧪 Route: native Codex trial\n🧠 Model: ${route.primaryModel}\n⚙️ Runtime: ${runtimeId}\n↩️ Pi fallback available: openai-codex/gpt-5.5 · runtime=pi`
      : `🛠 Route needs repair\nCurrent: ${route.primaryModel || "none"} · runtime=${runtimeId}\nExpected safe default: openai-codex/* · Pi\nTrial route: openai/* · Codex runtime`;
  const rows = profiles.map((profile) => {
    const marker = profile.active ? "✅" : "▫️";
    const planBadge = profilePlanBadge(status, profile);
    const exp = profile.expiresAt ? new Date(profile.expiresAt * 1000).toISOString().slice(0, 10) : "unknown";
    const refresh = profile.hasRefreshToken ? "refresh ok" : "no refresh token";
    return [
      `${marker} ${profile.slug}${planBadge ? ` ${planBadge}` : ""}${profile.active ? " · active" : ""}`,
      `🔐 ${refresh} · expires ${exp}`,
      usageLine(status, profile.slug),
    ].join("\n");
  });
  const pending = status.pendingDeviceAuth
    ? "\n\n🔑 Auth pending: open the link/code from the previous message, then press ✅ Check auth."
    : "";
  return [
    `🤖 GPT profile: ${status.active || "none"}`,
    routeLine,
    rows.length ? rows.join("\n\n") : "No profiles found in ~/.openclaw/codex-profiles.",
    pending.trim(),
  ].filter(Boolean).join("\n\n");
}

function scheduleRestart() {
  const child = spawn("bash", ["-lc", "sleep 1; systemctl --user restart openclaw-gateway.service"], {
    stdio: "ignore",
    detached: true,
  });
  child.unref();
}

function scheduleRestartAfterAutoswitch(config, autoswitched) {
  if (config.restartAfterSwitch && autoswitched?.switched) scheduleRestart();
}

function sessionStorePathForKey(sessionKey) {
  const match = String(sessionKey || "").match(/^agent:([a-z0-9._-]+):/i);
  if (!match) return "";
  return `${process.env.HOME || "/home/chip"}/.openclaw/agents/${match[1]}/sessions/sessions.json`;
}

function applySlashModelOverride(sessionKey, selection) {
  const path = sessionStorePathForKey(sessionKey);
  if (!path) return { ok: false, error: "missing session key" };
  const data = JSON.parse(readFileSync(path, "utf8"));
  const entry = data[sessionKey];
  if (!entry || typeof entry !== "object") return { ok: false, error: `session not found: ${sessionKey}` };
  copyFileSync(path, `${path}.bak-gptprof-model-switch-${Date.now()}`);
  if (selection.authProfile) {
    entry.authProfileOverride = selection.authProfile;
    entry.authProfileOverrideSource = "codex-profile-switcher";
  }
  entry.providerOverride = selection.provider;
  entry.modelOverride = selection.model;
  entry.modelOverrideSource = "user";
  if (selection.agentHarnessId) {
    entry.agentHarnessId = selection.agentHarnessId;
    entry.agentRuntime = { id: selection.agentHarnessId };
  } else {
    delete entry.agentRuntime;
  }
  if (selection.thinkingLevel) entry.thinkingLevel = selection.thinkingLevel;
  if (typeof selection.fastMode === "boolean") entry.fastMode = selection.fastMode;
  delete entry.model;
  delete entry.modelProvider;
  delete entry.contextTokens;
  delete entry.runtime;
  delete entry.runtimeId;
  delete entry.fallbackNoticeSelectedModel;
  delete entry.fallbackNoticeActiveModel;
  delete entry.fallbackNoticeReason;
  entry.updatedAt = Date.now();
  writeFileSync(path, `${JSON.stringify(data, null, 2)}\n`);
  return { ok: true };
}

function applySlashModelOverrideAfterFlush(sessionKey, selection) {
  setTimeout(() => {
    try {
      applySlashModelOverride(sessionKey, selection);
    } catch (error) {
      console.warn("[gptprof] delayed model switch patch failed", error);
    }
  }, 1_000).unref?.();
}

async function handleCommand(config) {
  if (!config.enabled) return { text: "GPT profile switcher is disabled." };
  const autoswitched = await managerJson({ ...config, timeoutMs: Math.min(config.timeoutMs || DEFAULT_TIMEOUT_MS, 8_000) }, ["autoswitch"]);
  const status = await managerJson(config, ["status"]);
  if (status.ok === false) return { text: `GPT profile status failed: ${status.error}` };
  return {
    text: `${autoswitchLine(autoswitched)}\n\n${statusText(status)}`,
    channelData: { telegram: { buttons: profileButtons(status) } },
  };
}

async function handleInboundClaim(event, context, config) {
  const text = eventText(event) || eventText(context);
  const command = text.startsWith("/") ? text.slice(1).split(/\s+/, 1)[0].split("@", 1)[0].toLowerCase() : "";
  if (command === "gptt" || command === "gptpro") {
    return await handleSessionAliasCommand(command, config, event, context);
  }
  if (command !== "gptprof") return { handled: false };
  const reply = await handleCommand(config);
  return { handled: true, text: reply.text, channelData: reply.channelData, reply };
}

function commandPartsFromText(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed.startsWith("/")) return null;
  const parts = trimmed.split(/\s+/);
  const command = parts[0].slice(1).split("@", 1)[0].toLowerCase();
  if (command !== "gptprof") return null;
  return parts.slice(1);
}

function slashCommandFromText(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed.startsWith("/")) return "";
  return trimmed.split(/\s+/, 1)[0].slice(1).split("@", 1)[0].toLowerCase();
}

function textOnlyStatus(status) {
  return statusText(status);
}

function activeAuthProfileFromStatus(status) {
  const activeProfile = Array.isArray(status.profiles) ? status.profiles.find((profile) => profile.active) : null;
  return activeProfile?.email ? `openai-codex:${activeProfile.email}` : "";
}

function sessionKeyFromEventOrContext(event, context) {
  const direct = event?.sessionKey || context?.sessionKey;
  if (direct) return direct;
  const senderId = event?.senderId || context?.senderId || event?.from || context?.from;
  const channel = event?.channel || context?.channel;
  if (channel === "telegram" && senderId) return `agent:chipdm:telegram:direct:${senderId}`;
  return "";
}

async function applySessionModelCommand(config, event, context, selection, label) {
  const autoswitched = await managerJson({ ...config, timeoutMs: 8_000 }, ["autoswitch"]);
  scheduleRestartAfterAutoswitch(config, autoswitched);
  const status = await managerJson({ ...config, timeoutMs: 8_000 }, ["status"]);
  const sessionKey = sessionKeyFromEventOrContext(event, context);
  const selectionWithAuth = {
    ...selection,
    authProfile: activeAuthProfileFromStatus(status),
  };
  const patched = applySlashModelOverride(sessionKey, selectionWithAuth);
  if (!patched.ok) return { handled: true, text: `GPT model switch failed: ${patched.error}` };
  applySlashModelOverrideAfterFlush(sessionKey, selectionWithAuth);
  return { handled: true, text: label(status) };
}

function sessionAliasSelection(command) {
  if (command === "gptt") {
    return {
      selection: {
        provider: "openai",
        model: "gpt-5.5",
        agentHarnessId: "codex",
        thinkingLevel: "medium",
        fastMode: true,
      },
      label: (status) => `Model set to gptt (openai/gpt-5.5) on Codex runtime with ${status.active || "active"} auth, thinking medium and fast on for this session.`,
    };
  }
  if (command === "gptpro") {
    return {
      selection: {
        provider: "openai",
        model: "gpt-5.5-pro",
        thinkingLevel: "high",
        fastMode: false,
      },
      label: (status) => `Model set to gptpro (openai/gpt-5.5-pro) with ${status.active || "active"} auth, thinking high and fast off for this session.`,
    };
  }
  return null;
}

async function handleSessionAliasCommand(command, config, event, context) {
  const alias = sessionAliasSelection(command);
  if (!alias) return { handled: false };
  return await applySessionModelCommand(config, event, context, alias.selection, alias.label);
}

async function handleTextCommand(args, config) {
  const fastConfig = { ...config, timeoutMs: Math.min(config.timeoutMs || DEFAULT_TIMEOUT_MS, 12_000) };
  const action = String(args[0] || "status").toLowerCase();
  if (action === "status") {
    const autoswitched = await managerJson({ ...fastConfig, timeoutMs: 8_000 }, ["autoswitch"]);
    scheduleRestartAfterAutoswitch(config, autoswitched);
    const status = await managerJson(fastConfig, ["status"]);
    if (status.ok === false) return `GPT profile status failed: ${status.error}`;
    return textOnlyStatus(status);
  }
  if (action === "refresh" || action === "usage") {
    const usage = await managerJson(config, ["usage"]);
    if (usage.ok === false) return `GPT profile usage refresh failed: ${usage.error}`;
    const status = await managerJson(fastConfig, ["status"]);
    return `${statusText(status)}\n\nUsage refreshed.`;
  }
  if (action === "autoswitch") {
    const autoswitched = await managerJson({ ...config, timeoutMs: 8_000 }, ["autoswitch"]);
    scheduleRestartAfterAutoswitch(config, autoswitched);
    const status = await managerJson(fastConfig, ["status"]);
    return `${autoswitchLine(autoswitched)}\n\n${statusText(status)}`;
  }
  if (action === "add" || action === "device-start") {
    const started = await managerJson(config, ["device-start"]);
    if (started.ok === false) return `OpenAI device auth failed to start: ${started.error}`;
    const expires = Math.max(1, Math.round((started.expiresInSeconds || 900) / 60));
    return [
      "OpenAI device auth",
      "",
      `Open: ${started.verificationUrl}`,
      `Code: ${started.userCode}`,
      `Expires in ${expires} minutes.`,
      "",
      "After approving it, send /gptprof check.",
    ].join("\n");
  }
  if (action === "check" || action === "device-check") {
    const checked = await managerJson(config, ["device-check"]);
    if (checked.ok === false) return `OpenAI device auth check failed: ${checked.error || "unknown error"}`;
    if (checked.pending) {
      return [
        "OpenAI device auth is still waiting.",
        "",
        `Open: ${checked.verificationUrl}`,
        `Code: ${checked.userCode}`,
        "",
        "Approve it, then send /gptprof check again.",
      ].join("\n");
    }
    if (config.restartAfterSwitch) scheduleRestart();
    return `Added and switched GPT profile to ${checked.active}. Gateway restart scheduled.`;
  }
  if (action === "use-pi" || action === "pi" || action === "route-pi" || action === "back-pi") {
    const routed = await managerJson(config, ["apply-pi-route"]);
    if (routed.ok === false) return `OpenAI-Codex Pi route failed: ${routed.error}`;
    if (config.restartAfterSwitch) scheduleRestart();
    return "OpenAI-Codex Pi route restored. Gateway restart scheduled.";
  }
  if (action === "use-native" || action === "native" || action === "route-native" || action === "try-codex" || action === "codex-runtime") {
    const routed = await managerJson(config, ["apply-native-route"]);
    if (routed.ok === false) return `Native Codex trial route failed: ${routed.error}`;
    if (config.restartAfterSwitch) scheduleRestart();
    return "Native Codex trial route applied: openai/gpt-5.5 with runtime=codex. Gateway restart scheduled.";
  }
  if (action === "switch") {
    const slug = String(args[1] || "").trim().toLowerCase();
    if (!slug || !/^[a-z0-9._-]+$/.test(slug)) return "Usage: /gptprof switch <slug>";
    const switched = await managerJson(config, ["switch", slug]);
    if (switched.ok === false) return `GPT profile switch failed: ${switched.error}`;
    if (config.restartAfterSwitch) scheduleRestart();
    return `Switched GPT profile to ${switched.active}. Gateway restart scheduled.`;
  }
  return "Usage: /gptprof [status|add|check|try-codex|use-pi|switch <slug>]";
}

async function handleBeforeDispatch(event, context, config) {
  // Global guard: for every inbound dispatch, check whether the active
  // OpenAI-Codex profile crossed the usage threshold.  This is deliberately
  // outside /gptprof so ordinary agent traffic also triggers the 95% failover.
  // codex-profile-manager uses a usage cache, so this is cheap on hot path.
  if (config.enabled) {
    const autoswitched = await managerJson({ ...config, timeoutMs: 8_000 }, ["autoswitch"]);
    scheduleRestartAfterAutoswitch(config, autoswitched);
  }

  const text = eventText(event) || eventText(context);
  const command = slashCommandFromText(text);
  const aliasHandled = await handleSessionAliasCommand(command, config, event, context);
  if (aliasHandled.handled) return aliasHandled;
  const args = commandPartsFromText(text);
  if (!args) return { handled: false };
  if (!config.enabled) return { handled: true, text: "GPT profile switcher is disabled." };
  const commandText = await handleTextCommand(args, config);
  const status = await managerJson({ ...config, timeoutMs: Math.min(config.timeoutMs || DEFAULT_TIMEOUT_MS, 8_000) }, ["status"]);
  const channelData = status.ok === false ? undefined : { telegram: { buttons: profileButtons(status) } };
  return { handled: true, text: commandText, channelData, reply: { text: commandText, channelData } };
}

const GptProfToolSchema = Type.Object({
  command: Type.Optional(Type.String({ description: "Raw /gptprof arguments." })),
  commandName: Type.Optional(Type.String({ description: "Slash command name." })),
  skillName: Type.Optional(Type.String({ description: "Skill name." })),
}, { additionalProperties: true });

function createGptProfTool(config) {
  return {
    name: "gptprof",
    label: "GPT Profile",
    description: "Manage OpenAI-Codex account profiles and the base Pi runtime route.",
    parameters: GptProfToolSchema,
    execute: async (_toolCallId, rawParams) => {
      const command = String(rawParams?.command || "").trim();
      const args = command ? command.split(/\s+/) : [];
      const text = await handleTextCommand(args, config);
      const status = await managerJson({ ...config, timeoutMs: Math.min(config.timeoutMs || DEFAULT_TIMEOUT_MS, 8_000) }, ["status"]);
      const channelData = status.ok === false ? undefined : { telegram: { buttons: profileButtons(status) } };
      return textResult(text, { ok: true, command: args }, channelData);
    },
  };
}

async function handleInteractive(ctx, config) {
  let slug = String(ctx?.callback?.payload || "").trim().toLowerCase();
  if (slug.startsWith("gptprof:")) slug = slug.slice("gptprof:".length);
  if (!slug || !/^[a-z0-9._-]+$/.test(slug)) {
    await ctx.respond?.editMessage?.({ text: "Bad GPT profile selection." });
    return { handled: true };
  }
  if (slug === "route-native") {
    const routed = await managerJson(config, ["apply-native-route"]);
    if (routed.ok === false) {
      await ctx.respond?.editMessage?.({ text: `Native Codex route failed: ${routed.error}` });
      return { handled: true };
    }
    const status = await managerJson(config, ["status"]);
    await ctx.respond?.editMessage?.({ text: `Native Codex trial route applied.\nModel: openai/gpt-5.5\nRuntime: codex\nGateway restart scheduled.\n\n${statusText(status)}`, buttons: profileButtons(status) });
    if (config.restartAfterSwitch) scheduleRestart();
    return { handled: true };
  }
  if (slug === "route-pi") {
    const routed = await managerJson(config, ["apply-pi-route"]);
    if (routed.ok === false) {
      await ctx.respond?.editMessage?.({ text: `OpenAI-Codex Pi route failed: ${routed.error}` });
      return { handled: true };
    }
    const status = await managerJson(config, ["status"]);
    await ctx.respond?.editMessage?.({ text: `OpenAI-Codex Pi route restored.\nGateway restart scheduled.\n\n${statusText(status)}`, buttons: profileButtons(status) });
    if (config.restartAfterSwitch) scheduleRestart();
    return { handled: true };
  }
  if (slug === "refresh") {
    const usage = await managerJson(config, ["usage"]);
    const status = await managerJson(config, ["status"]);
    const prefix = usage.ok === false ? `Usage refresh failed: ${usage.error}` : "Usage refreshed.";
    await ctx.respond?.editMessage?.({ text: `${prefix}\n\n${statusText(status)}`, buttons: profileButtons(status) });
    return { handled: true };
  }
  if (slug === "autoswitch") {
    const autoswitched = await managerJson({ ...config, timeoutMs: 8_000 }, ["autoswitch"]);
    scheduleRestartAfterAutoswitch(config, autoswitched);
    const status = await managerJson(config, ["status"]);
    await ctx.respond?.editMessage?.({ text: `${autoswitchLine(autoswitched)}\n\n${statusText(status)}`, buttons: profileButtons(status) });
    return { handled: true };
  }
  if (slug === "device-start") {
    const started = await managerJson(config, ["device-start"]);
    if (started.ok === false) {
      await ctx.respond?.editMessage?.({ text: `OpenAI device auth failed to start: ${started.error}` });
      return { handled: true };
    }
    const expires = Math.max(1, Math.round((started.expiresInSeconds || 900) / 60));
    const text = [
      "OpenAI device auth",
      "",
      `Open: ${started.verificationUrl}`,
      `Code: ${started.userCode}`,
      `Expires in ${expires} minutes.`,
      "",
      "After approving it, press Check auth.",
    ].join("\n");
    const status = await managerJson(config, ["status"]);
    await ctx.respond?.editMessage?.({ text, buttons: profileButtons(status) });
    return { handled: true };
  }
  if (slug === "device-check") {
    const checked = await managerJson(config, ["device-check"]);
    if (checked.ok === false) {
      await ctx.respond?.editMessage?.({ text: `OpenAI device auth check failed: ${checked.error || "unknown error"}` });
      return { handled: true };
    }
    if (checked.pending) {
      const text = [
        "OpenAI device auth is still waiting.",
        "",
        `Open: ${checked.verificationUrl}`,
        `Code: ${checked.userCode}`,
        "",
        "Approve it, then press Check auth again.",
      ].join("\n");
      const status = await managerJson(config, ["status"]);
      await ctx.respond?.editMessage?.({ text, buttons: profileButtons(status) });
      return { handled: true };
    }
    const status = await managerJson(config, ["status"]);
    await ctx.respond?.editMessage?.({ text: `Added and switched GPT profile to ${checked.active}.\nGateway restart scheduled.\n\n${statusText(status)}`, buttons: profileButtons(status) });
    if (config.restartAfterSwitch) scheduleRestart();
    return { handled: true };
  }
  const before = await managerJson(config, ["status"]);
  if (before.ok === false) {
    await ctx.respond?.editMessage?.({ text: `GPT profile status failed: ${before.error}` });
    return { handled: true };
  }
  if (slug === before.active) {
    await ctx.respond?.editMessage?.({ text: `Already using GPT profile ${slug}.\n\n${statusText(before)}`, buttons: profileButtons(before) });
    return { handled: true };
  }
  if (profileUsageOverThreshold(before, slug)) {
    const usage = Math.round(profileUsageMax(before, slug));
    const text = [
      `Not switching to ${slug}: usage is already ${usage}%.`,
      "Autoswitch threshold is 95%, so that profile would be switched away again before use.",
      "",
      statusText(before),
    ].join("\n");
    await ctx.respond?.editMessage?.({ text, buttons: profileButtons(before) });
    return { handled: true };
  }
  const switched = await managerJson(config, ["switch", slug]);
  if (switched.ok === false) {
    await ctx.respond?.editMessage?.({ text: `GPT profile switch failed: ${switched.error}` });
    return { handled: true };
  }
  const status = await managerJson(config, ["status"]);
  const text = `Switched GPT profile to ${switched.active}.\nGateway restart scheduled so every agent reloads auth state.\n\n${statusText(status)}`;
  await ctx.respond?.editMessage?.({ text, buttons: profileButtons(status) });
  if (config.restartAfterSwitch) scheduleRestart();
  return { handled: true };
}

const plugin = {
  id: "codex-profile-switcher",
  name: "GPT Profile Switcher",
  description: "Telegram /gptprof buttons for OpenAI-Codex account profiles with safe Pi route and native Codex runtime trial.",
  register(api) {
    const config = getConfig(api);
    if (typeof api.registerTool === "function") {
      api.registerTool(createGptProfTool(config), { name: "gptprof" });
    }
    api.registerCommand({
      name: "gptprof",
      description: "Switch OpenAI-Codex account profiles and try native Codex runtime without changing the safe Pi fallback.",
      acceptsArgs: false,
      handler: async () => await handleCommand(config),
    });
    api.registerCommand({
      name: "gptt",
      description: "Switch this Telegram session to openai/gpt-5.5 on the native Codex runtime with medium thinking and fast mode.",
      acceptsArgs: false,
      handler: async (ctx) => {
        const result = await handleSessionAliasCommand("gptt", config, ctx, ctx);
        return { text: result.text || "GPT model switch failed: no response." };
      },
    });
    api.registerCommand({
      name: "gptpro",
      description: "Switch this Telegram session to openai/gpt-5.5-pro on the native Codex runtime.",
      acceptsArgs: false,
      handler: async (ctx) => {
        const result = await handleSessionAliasCommand("gptpro", config, ctx, ctx);
        return { text: result.text || "GPT model switch failed: no response." };
      },
    });
    if (typeof api.registerInteractiveHandler === "function") {
      api.registerInteractiveHandler({
        channel: "telegram",
        namespace: "gptprof",
        handler: async (ctx) => await handleInteractive(ctx, config),
      });
    }
    if (typeof api.on === "function") {
      api.on("inbound_claim", async (event, context) => await handleInboundClaim(event, context, config), { priority: 900 });
      api.on("before_dispatch", async (event, context) => await handleBeforeDispatch(event, context, config), { priority: 900 });
    } else if (typeof api.registerHook === "function") {
      api.registerHook(
        "inbound_claim",
        async (event, context) => await handleInboundClaim(event, context, config),
        {
          name: "gpt-profile-switcher-inbound-claim",
          description: "Route Telegram /gptprof to GPT profile switcher.",
        },
      );
      api.registerHook(
        "before_dispatch",
        async (event, context) => await handleBeforeDispatch(event, context, config),
        {
          name: "gpt-profile-switcher-before-dispatch",
          description: "Handle Telegram /gptprof before agent dispatch.",
        },
      );
    }
    const payload = { event: "gpt_profile_switcher.registered", enabled: config.enabled, managerPath: config.managerPath };
    if (api?.logger?.info) api.logger.info(payload);
    else console.info(JSON.stringify(payload));
  },
};

export { handleBeforeDispatch, handleCommand, handleInboundClaim, handleInteractive, profileButtons, statusText };
export default plugin;
