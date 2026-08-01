/** A tiny provider chip: our own mark in each provider's signature colour,
 *  never their trademarked logo, and drawn inline so the dashboard still
 *  makes zero network requests. Local models get their own purple mark —
 *  "this ran on your machine" is the most useful thing a glance can say. */

const LOCAL_FAMILIES = [
  "qwen", "llama", "mistral-7b", "mixtral", "gemma", "phi", "deepseek",
  "codellama", "starcoder", "granite", "smol", "tinyllama", "nous", "hermes",
];

interface Mark { key: string; label: string; letter: string; }

export function classifyProvider(provider?: string | null, model?: string | null): Mark {
  const m = (model ?? "").toLowerCase();
  const p = (provider ?? "").toLowerCase();
  const letter = (name: string) => name.slice(0, 1).toUpperCase();

  // A local model can arrive tagged "openai" (LM Studio speaks that format),
  // so the model name decides before the wire format does.
  if (LOCAL_FAMILIES.some((f) => m.includes(f)) || m.includes("local")) {
    const family = m.split(/[/:\-]/).filter(Boolean)[0] ?? "local";
    return { key: "local", label: `${family}, running locally`, letter: letter(family) };
  }
  if (m.startsWith("claude") || p === "anthropic") {
    return { key: "anthropic", label: "Anthropic", letter: "A" };
  }
  if (m.startsWith("gpt") || m.startsWith("o1") || m.startsWith("o3") ||
      m.startsWith("o4") || m.startsWith("chatgpt") || m.startsWith("text-embedding") ||
      p === "openai") {
    return { key: "openai", label: "OpenAI", letter: "O" };
  }
  if (m.startsWith("gemini") || m.startsWith("palm") || p === "google") {
    return { key: "google", label: "Google", letter: "G" };
  }
  if (m.startsWith("mistral") || m.startsWith("magistral") || p === "mistral") {
    return { key: "mistral", label: "Mistral", letter: "M" };
  }
  if (m.startsWith("grok") || p === "xai") return { key: "xai", label: "xAI", letter: "X" };
  if (m.startsWith("command") || p === "cohere") {
    return { key: "cohere", label: "Cohere", letter: "C" };
  }
  return { key: "generic", label: provider || "unknown provider", letter: letter(m || "?") };
}

export default function ProviderMark({ provider, model }: {
  provider?: string | null; model?: string | null;
}) {
  const mark = classifyProvider(provider, model);
  return (
    <span className={`pmark pm-${mark.key}`} title={mark.label} aria-label={mark.label}>
      {mark.letter}
    </span>
  );
}
