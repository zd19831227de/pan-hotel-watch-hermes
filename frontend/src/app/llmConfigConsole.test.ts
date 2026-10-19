import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("./LlmConfigConsole.tsx", import.meta.url),
  "utf8",
);
const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const stylesSource = readFileSync(
  new URL("../styles/global.css", import.meta.url),
  "utf8",
);

test("LLM console does not reload protected APIs for every access-key keystroke", () => {
  assert.match(source, /const loadConsole = useCallback\(async \(apiKey: string\)/);
  assert.match(
    source,
    /void loadConsole\(props\.supervisorApiKey\.trim\(\)\)/,
  );
  assert.doesNotMatch(
    source,
    /useEffect\(\(\) => \{\s*void refresh\(\);\s*\}, \[refresh\]\)/,
  );
});

test("LLM console keeps model catalog fetch clickable so missing access is explained", () => {
  assert.match(source, /const canManageModels = Boolean\(activeAccessKey\)/);
  const fetchSource = source.slice(
    source.indexOf('>拉取模型库</button>') - 700,
    source.indexOf('>拉取模型库</button>') + 20,
  );
  assert.doesNotMatch(fetchSource, /!canManageModels/);
  assert.match(source, /if \(!activeAccessKey\)/);
});

test("new LLM console does not mount the legacy unauthenticated model chain", () => {
  assert.doesNotMatch(appSource, /<LlmModelChainPanel\s*\/>/);
});

test("provider and model-kind switches clear the previous supplier key", () => {
  const switchKindSource = source.slice(
    source.indexOf("function switchKind"),
    source.indexOf("function applyTemplate"),
  );
  const applyTemplateSource = source.slice(
    source.indexOf("function applyTemplate"),
    source.indexOf("async function handleFetchModels"),
  );
  assert.match(switchKindSource, /api_key: ""/);
  assert.doesNotMatch(switchKindSource, /current\.api_key/);
  assert.match(applyTemplateSource, /api_key: ""/);
});

test("editing requests reuse the saved key only through the selected model id", () => {
  const testProviderSource = source.slice(
    source.indexOf("async function handleTestProvider"),
    source.indexOf("async function handleTestSelectedEmbedding"),
  );
  assert.match(testProviderSource, /model_config_id: editingChatId \?\? undefined/);
});

test("manual provider or endpoint edits clear the currently entered supplier key", () => {
  assert.match(
    source,
    /provider: event\.target\.value,\s*api_key: ""/,
  );
  assert.match(
    source,
    /base_url: event\.target\.value,\s*api_key: ""/,
  );
});

test("Hermes resync remains available for clearing an empty model chain", () => {
  const selectionSource = source.slice(
    source.indexOf('<section className="panel llm-selection-panel">'),
    source.indexOf('<details className="advanced-details llm-legacy-config">'),
  );
  const syncButton = selectionSource.slice(
    selectionSource.lastIndexOf('<button'),
    selectionSource.lastIndexOf('</button>'),
  );
  assert.doesNotMatch(syncButton, /chatModels\.length === 0/);
});

test("LLM console defers supervisor access key to advanced configuration (no local input)", () => {
  // Problem 3: the LLM config page must not keep a redundant Supervisor key
  // input of its own. The single source of truth is the 高级配置 panel, which
  // writes back to App-level supervisorApiKey. LLM config reuses it verbatim.
  assert.match(source, /const activeAccessKey = props\.supervisorApiKey\.trim\(\)/);
  assert.doesNotMatch(source, /setAccessKey/);
  const accessPanel = source.slice(
    source.indexOf('className="panel llm-access-key-panel"'),
    source.indexOf('className="panel llm-supplier-panel"'),
  );
  assert.doesNotMatch(accessPanel, /<input/);
});

test("advanced maintenance aligns the LLM workspace with advanced configuration", () => {
  const layoutRule =
    stylesSource.match(
      /\.advanced-maintenance \.model-config-column\s*\{([^}]*)\}/,
    )?.[1] ?? "";

  assert.match(layoutRule, /width:\s*100%/);
  assert.match(layoutRule, /max-width:\s*none/);
  assert.match(layoutRule, /margin:\s*14px 0 0/);
});
