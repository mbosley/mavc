// Exercise a catalog provider through the public SDK, replacing fetch in memory.
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {findPackageJSON} from 'node:module';
const root = process.env.MAVC_PI_ROOT;
let sent = 0, modelId;
const urls = [];
globalThis.fetch = async url => {
  sent++;
  urls.push(String(url));
  const chunk = (delta, finish_reason, usage) => 'data: ' + JSON.stringify({id: 'example-response',
    object: 'chat.completion.chunk', created: 0, model: modelId,
    choices: [{index: 0, delta, finish_reason}], ...(usage ? {usage} : {})}) + '\n\n';
  return new Response(chunk({role: 'assistant', content: '{"answer":"example"}'}, null)
    + chunk({}, 'stop', {prompt_tokens: 1, completion_tokens: 1, total_tokens: 2}) + 'data: [DONE]\n\n',
    {status: 200, headers: {'content-type': 'text/event-stream'}});
};
const sdk = await import(pathToFileURL(path.join(root, 'node_modules/@earendil-works/pi-coding-agent/dist/index.js')));
const manifest = path.join(root, 'node_modules/@earendil-works/pi-coding-agent/package.json');
const ai = await import(pathToFileURL(path.join(path.dirname(findPackageJSON('@earendil-works/pi-ai', manifest)), 'dist/index.js')));
const runtime = await sdk.ModelRuntime.create({credentials: new ai.InMemoryCredentialStore(),
  modelsStore: new ai.InMemoryModelsStore(), modelsPath: null, refreshOnCreate: false, allowModelNetwork: false});
const selected = runtime.getModels('groq').find(model => model.api === 'openai-completions' && !model.reasoning);
if (!selected) throw new Error('Public Pi catalog has no suitable non-reasoning completion model for this test');
modelId = selected.id;
const {runPi} = await import(pathToFileURL(process.argv[2]));
const request = {node: {provider: selected.provider, model: modelId, reasoning: 'off',
  harness_retry: {agent_enabled: false, provider_max_retries: 0},
  settings: {tools: [], transport: 'sse', compaction: false, cache_warming: 'off'}},
  harness_version: '1.4.0', system_prompt: 'Return a JSON object with an answer string.', prompt: 'A generic example.'};
const response = await runPi(request, root, () => {throw new Error('This test declares no tools');});
process.stdout.write(JSON.stringify({request, response, sent, urls}));
