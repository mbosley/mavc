// A public Pi SDK session; Python remains the owner of tools and scientific checks.
import fs from 'node:fs';
import path from 'node:path';
import {findPackageJSON} from 'node:module';
import {pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';
import {once} from 'node:events';

const VERSION = '1.4.0';
const clone = value => JSON.parse(JSON.stringify(value));

export async function runPi(request, root, dispatch) {
  const manifest = path.join(root, 'node_modules/@earendil-works/pi-coding-agent/package.json');
  const version = JSON.parse(fs.readFileSync(manifest)).version;
  if (version !== VERSION || (request.harness_version && request.harness_version !== version))
    throw new Error(`Adapter supports public Pi ${VERSION}; declared ${request.harness_version ?? VERSION}, installed ${version}`);
  const sdk = await import(pathToFileURL(path.join(path.dirname(manifest), 'dist/index.js')));
  const aiManifest = findPackageJSON('@earendil-works/pi-ai', manifest);
  const ai = await import(pathToFileURL(path.join(path.dirname(aiManifest), 'dist/index.js')));
  const node = request.node, knobs = node.settings ?? {};
  const {recorded_turns: recording, ...overrides} = node.harness_settings ?? {};
  const runtime = await sdk.ModelRuntime.create({credentials: new ai.InMemoryCredentialStore(),
    modelsStore: new ai.InMemoryModelsStore(), modelsPath: null, refreshOnCreate: false, allowModelNetwork: false});
  if (recording !== undefined) {
    if (node.provider !== 'faux' || !Array.isArray(recording))
      throw new Error('recorded_turns requires provider=faux and a list of scripted assistant turns');
    // A provider-free public Pi session, not a simulated Python tool loop.
    globalThis.fetch = async () => { throw new Error('Network is disabled for recorded Pi sessions'); };
    const faux = ai.fauxProvider({provider: node.provider, models: [{id: node.model, reasoning: true}]});
    faux.setResponses(recording.map((turn, index) => ai.fauxAssistantMessage(
      turn.tools ? turn.tools.map((tool, i) => ai.fauxToolCall(tool.name, tool.input, {id: `turn-${index}-${i}`}))
        : turn.text ?? '', {stopReason: turn.tools ? 'toolUse' : 'stop'})));
    runtime.registerNativeProvider(faux.provider);
  }
  const model = runtime.getModel(node.provider, node.model);
  if (!model) throw new Error('Model is absent from the public Pi catalog; use its exact provider and model ID');
  const retry = node.harness_retry ?? {};
  const settings = sdk.SettingsManager.inMemory({compaction: {enabled: knobs.compaction ?? false},
    cacheWarming: knobs.cache_warming ?? 'off', transport: knobs.transport ?? 'sse',
    retry: {enabled: retry.agent_enabled ?? false, maxRetries: 0,
      provider: {maxRetries: retry.provider_max_retries ?? 0}},
    ...overrides, enableAnalytics: false, enableInstallTelemetry: false});
  const tools = request.tool_manifest ?? [];
  if (tools.some(tool => tool.origin !== 'python')) throw new Error('Pi adapter supports declared Python tools only');
  let session, closure = null;
  const invoked = new Set(), frames = new Map();
  const exchange = async frame => {
    const reply = await dispatch(frame);
    if (reply.closed || reply.fatal) {
      if (!closure || reply.fatal) closure = reply;
      if (reply.fatal) session.agent.abort();
    }
    return reply;
  };
  const customTools = tools.map(tool => ({name: tool.name, label: tool.name, description: tool.description,
    parameters: tool.input_schema, defaultActive: tool.active, executionMode: 'sequential',
    async execute(id, input) {
      invoked.add(id);
      const reply = await exchange({...frames.get(id), phase: 'invoke'});
      return {content: [{type: 'text', text: JSON.stringify(reply)}], details: reply, isError: !reply.accepted, terminate: reply.closed || reply.fatal};
    }}));
  const extensions = {extensions: [], errors: [], runtime: sdk.createExtensionRuntime()};
  const resourceLoader = {
    getExtensions: () => extensions,
    getSkills: () => ({skills: [], diagnostics: []}), getPrompts: () => ({prompts: [], diagnostics: []}),
    getThemes: () => ({themes: [], diagnostics: []}), getAgentsFiles: () => ({agentsFiles: []}),
    getSystemPrompt: () => request.system_prompt, getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [], getAppendSystemPromptSources: () => [],
    extendResources: () => {}, reload: async () => {},
  };
  ({session} = await sdk.createAgentSession({modelRuntime: runtime, model, thinkingLevel: node.reasoning,
    tools: tools.length ? tools.filter(tool => tool.active).map(tool => tool.name) : knobs.tools ?? [],
    customTools, resourceLoader, settingsManager: settings, sessionManager: sdk.SessionManager.inMemory()}));
  const providerRequests = [], sessionRequests = [];
  let responseStatus;
  try {
    if (session.thinkingLevel !== node.reasoning) throw new Error(`Pi selected thinking level ${session.thinkingLevel}; declared ${node.reasoning}`);
    const active = session.getActiveToolNames().slice().sort();
    if (JSON.stringify(active) !== JSON.stringify((knobs.tools ?? []).slice().sort()))
      throw new Error('Pi active tools differ from settings.tools; declare the manifest tool names explicitly');
    session.agent.toolExecution = 'sequential';
    const finish = session.agent.finishTurn;
    session.agent.finishTurn = (...args) => closure ? {action: 'end'} : finish?.(...args);
    // Preserve transcript tool declarations, but send only the caller's system text (no local cwd/context files).
    const transform = session.agent.transformContext;
    session.agent.transformContext = async (messages, signal) => (await transform(messages, signal)).map(message =>
      message.role === 'system' ? {...message, content: request.system_prompt, sections: undefined} : message);
    const stream = session.agent.streamFunction;
    session.agent.streamFunction = (selected, context, options) => {
      const effective = {...options, ...(knobs.max_output_tokens ? {maxTokens: knobs.max_output_tokens} : {}),
        ...(knobs.temperature !== undefined ? {temperature: knobs.temperature} : {})};
      sessionRequests.push({model: selected.id, reasoning: {effort: effective.reasoning ?? 'off'},
        system_prompt: context.messages.filter(message => message.role === 'system').map(ai.getSystemMessageText).join('\n'),
        transport: settings.getTransport(), max_output_tokens: effective.maxTokens, temperature: effective.temperature,
        tools: session.getActiveToolNames().map(name => ({name}))});
      return stream(selected, context, effective);
    };
    session.agent.onPayload = payload => { providerRequests.push(clone(payload)); return payload; };
    session.agent.onResponse = response => { responseStatus = response.status; };
    session.agent.subscribe(async event => {
      if (event.type === 'tool_execution_start') {
        const frame = {type: 'mavc_tool_call', id: event.toolCallId, parent_id: null,
          name: event.toolName, input: event.args};
        frames.set(frame.id, frame);
        await exchange({...frame, phase: 'begin'});
      } else if (event.type === 'tool_execution_end') {
        const frame = frames.get(event.toolCallId);
        await exchange({...frame, phase: invoked.has(frame.id) ? 'observe_result' : 'sdk_rejected',
          result: event.result, isError: event.isError});
      }
    });
    await session.prompt(request.prompt, {expandPromptTemplates: false});
    while (tools.length && !closure) {
      const last = session.messages.filter(message => message.role === 'assistant').at(-1);
      if (last?.stopReason !== 'stop' || last.errorMessage) break;
      const next = await exchange({type: 'mavc_session_stopped', id: null, stop_reason: 'stop'});
      if (!next.followup || closure) break;
      await session.prompt(next.followup, {expandPromptTemplates: false});
    }
    const messages = session.messages.filter(message => message.role === 'assistant');
    const last = messages.at(-1);
    const accepted = closure?.terminal && closure.accepted && closure.closed && !closure.fatal;
    return {proposal: accepted ? closure.result : null, text: session.getLastAssistantText(),
      stop_reason: tools.length ? (accepted ? 'stop' : 'error') : last?.stopReason,
      error: closure?.fatal ? closure.error?.message : accepted ? null : tools.length ? 'No checked submission accepted' : last?.errorMessage ?? null,
      mode: recording === undefined ? 'live' : 'recorded', session_stop_reason: last?.stopReason,
      provider_requests: providerRequests, session_requests: sessionRequests, messages,
      observed: {model: session.model.id, provider: session.model.provider, reasoning: session.thinkingLevel,
        harness_version: version, active_tools: session.getActiveToolNames(), request: sessionRequests.at(-1),
        response: responseStatus === undefined ? {} : {status: responseStatus}, resolved_settings: {
          compaction: settings.getCompactionSettings(session.model), retry: settings.getRetrySettings(),
          provider_retry: settings.getProviderRetrySettings(), cache_warming: settings.getCacheWarmingMode(),
          transport: settings.getTransport()}}};
  } finally { session.dispose(); }
}

async function main() {
  const reader = createInterface({input: process.stdin});
  const lines = reader[Symbol.asyncIterator]();
  const read = async () => {
    const next = await lines.next();
    if (next.done) throw new Error('Unexpected Python tool pipe EOF');
    return JSON.parse(next.value);
  };
  const write = async frame => {
    if (!process.stdout.write(JSON.stringify(frame) + '\n')) await once(process.stdout, 'drain');
  };
  try {
    const first = await read();
    if (first.type !== 'mavc_call') throw new Error('Expected initial MAVC request');
    const dispatch = async frame => {
      await write(frame);
      const reply = await read();
      if (reply.type !== 'mavc_tool_result' || reply.id !== frame.id) throw new Error('Invalid Python tool reply');
      return reply.result;
    };
    await write({type: 'mavc_response', response: await runPi(first.request, process.argv[2], dispatch)});
  } finally { reader.close(); process.stdin.destroy(); }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main().catch(error => {
  process.stderr.write(String(error.message).replace(/bearer\s+\S+|\bsk-[\w-]{16,}/gi, '[redacted]') + '\n');
  process.exitCode = 1;
});
