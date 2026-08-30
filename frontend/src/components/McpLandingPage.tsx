import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode, RefObject } from 'react';
import { Link } from 'react-router-dom';
import { Check, Copy, Lock } from 'lucide-react';
import { ROUTES } from '../routes';
import '../styles/mcp-landing.css';

/**
 * Marketing page for the Redirx MCP server.
 *
 * Isolated draft: lives at ROUTES.mcpPreview, is not linked from any nav, and
 * shares no styling with the rest of the app. Built to
 * docs/design/mcp-landing-brief.md; the accent, neutrals, type scale and
 * motion timings all come from that brief's §2 and are scoped to the
 * `.mcp-landing` wrapper so the global tokens in styles/globals.css keep
 * resolving exactly as they do everywhere else.
 */

// Placeholder host. render.yaml ships MCP_PUBLIC_URL with `sync: false`, so no
// real host is committed to git yet (brief §5, §8.1). Swap before publishing.
const MCP_HOST_URL = 'https://mcp.redirx.dev/mcp';

const CONNECT_COMMAND = `claude mcp add --transport http redirx ${MCP_HOST_URL} \\
  --header "Authorization: Bearer rdx_your_key_here"`;

interface TranscriptEntry {
  call: string;
  results: string[];
}

// Illustrative, and captioned as such under the card. Field names are verbatim
// from the tool schemas.
const HERO_TRANSCRIPT: TranscriptEntry[] = [
  {
    call: '→ discover  domain: "oldshop.com", side: "old"',
    results: ['Found 412 URLs on https://oldshop.com via sitemap.'],
  },
  {
    call: '→ deep_match  old_urls: [412], new_urls: [389]',
    results: ['Started migration 7f3a… (status: pending)'],
  },
  {
    call: '→ deep_match  migration_id: "7f3a…"',
    results: ['Migration 7f3a…: completed (done)', 'Total matches: 397.'],
  },
  {
    call: '→ preview  migration_id: "7f3a…"',
    results: ['397 matches (312 high, 61 medium, 24 low; 44 flagged for review).'],
  },
];

interface ToolCard {
  name: string;
  copy: string;
  paid: boolean;
}

const TOOLS: ToolCard[] = [
  {
    name: 'discover',
    copy:
      "Enumerates a site's page URLs from a root domain — sitemap first, then the CMS's own API (WordPress, Shopify), then a crawl. Seconds, not minutes. Run it once for the old site, once for the new.",
    paid: false,
  },
  {
    name: 'deep_match',
    copy:
      'The full content-matching engine. Scrapes and compares actual page content, not just URL similarity. Runs on your full URL set, at full quality, on every plan. Starts a background run and returns a migration_id you poll.',
    paid: false,
  },
  {
    name: 'preview',
    copy:
      'Match count, a high/medium/low confidence breakdown, how many old URLs found nothing, and ~20 sample pairings — deliberately half from the bottom of the confidence range, not a highlight reel.',
    paid: false,
  },
  {
    name: 'export',
    copy:
      'The deploy-ready redirect file: Apache, nginx, WordPress, Vercel, Cloudflare, Shopify, CSV, or JSON. Filter by minimum confidence, choose paths or absolute URLs.',
    paid: true,
  },
];

interface SequenceStep {
  title: string;
  calls: string[];
  body: ReactNode;
}

const SEQUENCE: SequenceStep[] = [
  {
    title: 'Discover both sides.',
    calls: ['discover  side: "old"', 'discover  side: "new"'],
    body: (
      <>
        <code>discover</code> twice — once with <code>side: "old"</code>, once with{' '}
        <code>side: "new"</code>. Its <code>urls</code> output is exactly{' '}
        <code>deep_match</code>'s input.
      </>
    ),
  },
  {
    title: 'Start the match.',
    calls: [
      'deep_match  old_urls, new_urls  →  migration_id',
      'deep_match  migration_id  →  done: false',
    ],
    body: (
      <>
        <code>deep_match</code> with <code>old_urls</code> and <code>new_urls</code> returns a{' '}
        <code>migration_id</code> immediately. A real site takes minutes — the engine is scraping
        every page — so the agent polls the same tool with just the <code>migration_id</code> until{' '}
        <code>done</code> is true.
      </>
    ),
  },
  {
    title: 'Read the result.',
    calls: ['preview  migration_id'],
    body: (
      <>
        <code>preview</code> returns aggregates and a sample weighted toward the shaky matches, so
        nobody approves a file on the strength of its best rows.
      </>
    ),
  },
  {
    title: 'Export.',
    calls: ['export  migration_id, format'],
    body: (
      <>
        <code>export</code> returns the redirect file in your target format. This is the paid step,
        and the first one.
      </>
    ),
  },
];

interface CompareRow {
  label: string;
  usually: boolean;
  here: boolean;
}

const COMPARE_ROWS: CompareRow[] = [
  { label: 'Full URL set', usually: false, here: true },
  { label: 'Full matching quality', usually: false, here: true },
  { label: 'Confidence breakdown', usually: false, here: true },
  { label: 'Sample of weak matches', usually: false, here: true },
  { label: 'Deploy-ready file', usually: false, here: false },
];

interface FaqItem {
  question: string;
  answer: ReactNode;
}

const FAQ: FaqItem[] = [
  {
    question: 'Will my agent change my site without me?',
    answer: (
      <>
        No. None of the four tools touch your site. <code>discover</code> reads public URLs,{' '}
        <code>deep_match</code> scrapes pages, <code>preview</code> and <code>export</code> read
        results back. The redirect file is handed to you as text — installing it is a step you take.
      </>
    ),
  },
  {
    question: 'Can my agent spend money on its own?',
    answer: (
      <>
        No. When <code>export</code> requires payment it returns a checkout URL that must be opened
        by a person in a browser. Settlement is human-in-the-loop Stripe Checkout; the agent has no
        ability to complete it.
      </>
    ),
  },
  {
    question: 'Is the free matching a limited version of the real thing?',
    answer: (
      <>
        It's the same engine, the same pipeline, on your full URL set. Nothing about match quality
        varies by plan. What's limited is how many Deep Match runs a free account can start in a
        rolling 24-hour window — and that returns a clear "retry later," not a degraded result.
      </>
    ),
  },
  {
    question: 'Do I need to install anything?',
    answer: (
      <>
        No. It's a remote server over Streamable HTTP — no stdio wrapper, no npx package, no local
        binary. You add a URL and a bearer token.
      </>
    ),
  },
  {
    question: 'What formats does the export produce?',
    answer: (
      <>
        Apache, nginx, WordPress, Vercel, Cloudflare, Shopify, CSV, and JSON. You can also set a
        minimum confidence to drop weak matches, and choose between request paths and absolute URLs.
      </>
    ),
  },
];

function revealDelay(index: number): CSSProperties {
  return { '--mcp-reveal-delay': `${index * 60}ms` } as CSSProperties;
}

/**
 * Entrance reveals, per brief §2.5: opacity + 12px rise, 60ms stagger, fired
 * by IntersectionObserver at 20% visibility, once, never re-triggered.
 *
 * The hidden state only exists while `.js-motion` is on the root, and this
 * effect refuses to add that class under `prefers-reduced-motion: reduce` or
 * without IntersectionObserver. Content is therefore always in the DOM and
 * always readable, script or no script.
 */
function useEntranceReveals(rootRef: RefObject<HTMLDivElement>) {
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    if (typeof IntersectionObserver === 'undefined') return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;

    root.classList.add('js-motion');

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          // 20% of the element, per the brief. An element taller than the
          // viewport can never reach that ratio (the sequence card on a
          // phone is several screens tall), so 20% of the viewport counts
          // too. Without this the sequence stays invisible on small screens.
          const enough =
            entry.intersectionRatio >= 0.2 ||
            entry.intersectionRect.height >= window.innerHeight * 0.2;
          if (!enough) continue;
          entry.target.classList.add('is-in');
          observer.unobserve(entry.target);
        }
      },
      { threshold: [0, 0.2] }
    );

    const targets = Array.from(root.querySelectorAll('[data-reveal]'));
    targets.forEach((el) => observer.observe(el));

    // Failsafe. If the observer has reported nothing at all by the time the
    // page has settled, it is not working (throttled, or an environment that
    // stubs it out) and the entrance would leave the page blank. Drop the
    // animation rather than the content.
    const failsafe = window.setTimeout(() => {
      if (targets.some((el) => el.classList.contains('is-in'))) return;
      observer.disconnect();
      root.classList.remove('js-motion');
    }, 1500);

    return () => {
      window.clearTimeout(failsafe);
      observer.disconnect();
      root.classList.remove('js-motion');
    };
  }, [rootRef]);
}

type CopyState = 'idle' | 'copied' | 'failed';

const COPY_LABEL: Record<CopyState, string> = {
  idle: 'Copy',
  copied: 'Copied',
  failed: 'Select and copy',
};

const COPY_ANNOUNCEMENT: Record<CopyState, string> = {
  idle: '',
  copied: 'Connect command copied to clipboard.',
  failed: 'Could not reach the clipboard. The command is selected, copy it manually.',
};

function CopyCommandButton({ value, codeRef }: { value: string; codeRef: RefObject<HTMLElement> }) {
  const [state, setState] = useState<CopyState>('idle');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const resetLater = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState('idle'), 3000);
  }, []);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setState('copied');
    } catch {
      // Clipboard access can be refused (permissions, an unfocused document,
      // an insecure origin). Say so and select the text so the keyboard
      // shortcut still works, rather than failing silently.
      setState('failed');
      const node = codeRef.current;
      if (node && window.getSelection) {
        const range = document.createRange();
        range.selectNodeContents(node);
        const selection = window.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
      }
    }
    resetLater();
  }, [value, codeRef, resetLater]);

  return (
    <button
      type="button"
      className="mcp-copy"
      onClick={handleCopy}
      aria-label="Copy the connect command to the clipboard"
    >
      {state === 'copied' ? (
        <Check size={14} aria-hidden="true" />
      ) : (
        <Copy size={14} aria-hidden="true" />
      )}
      <span aria-hidden="true">{COPY_LABEL[state]}</span>
      <span className="mcp-sr-only" role="status" aria-live="polite">
        {COPY_ANNOUNCEMENT[state]}
      </span>
    </button>
  );
}

export function McpLandingPage() {
  const rootRef = useRef<HTMLDivElement>(null);
  const connectCodeRef = useRef<HTMLElement>(null);
  useEntranceReveals(rootRef);

  useEffect(() => {
    const previous = document.title;
    document.title = 'Redirx MCP server';
    return () => {
      document.title = previous;
    };
  }, []);

  return (
    <div className="mcp-landing" ref={rootRef}>
      <header className="mcp-masthead">
        <div className="mcp-container mcp-masthead__inner">
          <Link to={ROUTES.mcpPreview} className="mcp-wordmark">
            Redirx
          </Link>
        </div>
      </header>

      <main>
        {/* ---------- Hero ---------- */}
        <section className="mcp-hero">
          <div className="mcp-container mcp-hero__grid">
            <div className="mcp-hero__copy">
              <p className="mcp-badge" data-reveal style={revealDelay(0)}>
                Introducing the Redirx MCP server
              </p>
              <h1 className="mcp-h1" data-reveal style={revealDelay(1)}>
                Point your agent at the old site. Get the redirect file.
              </h1>
              <p className="mcp-lead mcp-lead--hero" data-reveal style={revealDelay(2)}>
                Four MCP tools — discover, deep match, preview, export. The matching engine runs
                free at full quality on every plan. You pay for the deploy-ready file, and only when
                you want it.
              </p>
              <div className="mcp-hero__ctas" data-reveal style={revealDelay(3)}>
                <Link to={ROUTES.apiKeys} className="mcp-btn mcp-btn--primary">
                  Get an API key
                </Link>
                <a href="#the-toolset" className="mcp-btn mcp-btn--ghost">
                  See the four tools
                </a>
              </div>
              <p className="mcp-meta" data-reveal style={revealDelay(4)}>
                Streamable HTTP. No npx, no local binary, nothing to install.
              </p>
            </div>

            <figure className="mcp-card mcp-card--artifact" data-reveal style={revealDelay(2)}>
              <div className="mcp-transcript">
                {HERO_TRANSCRIPT.map((entry) => (
                  <div key={entry.call}>
                    <p className="mcp-transcript__call">{entry.call}</p>
                    {entry.results.map((line) => (
                      <p key={line} className="mcp-transcript__result">
                        {line}
                      </p>
                    ))}
                  </div>
                ))}
              </div>
              <figcaption className="mcp-transcript__caption">
                An example run. The field names are the real ones; the counts are illustrative.
              </figcaption>
            </figure>
          </div>
        </section>

        {/* ---------- The four tools ---------- */}
        <section className="mcp-section mcp-band" id="the-toolset" aria-labelledby="toolset-heading">
          <div className="mcp-container">
            <div className="mcp-section-head" data-reveal>
              <span className="mcp-eyebrow">The toolset</span>
              <h2 className="mcp-h2" id="toolset-heading">
                Four tools. Only one of them is paid.
              </h2>
              <p className="mcp-lead">
                Matching quality is never gated. Paying doesn't change what was matched — it unlocks
                the file you install.
              </p>
            </div>

            <div className="mcp-tools">
              {TOOLS.map((tool, index) => (
                <article
                  key={tool.name}
                  className={`mcp-card mcp-tool${tool.paid ? ' mcp-tool--emphasis' : ''}`}
                  data-reveal
                  style={revealDelay(index)}
                >
                  <div className="mcp-tool__head">
                    <h3 className="mcp-tool__name">{tool.name}</h3>
                    <span className={`mcp-chip ${tool.paid ? 'mcp-chip--paid' : 'mcp-chip--free'}`}>
                      {tool.paid ? 'Paid' : 'Free'}
                    </span>
                  </div>
                  <p className="mcp-body">{tool.copy}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* ---------- The sequence ---------- */}
        <section className="mcp-section" aria-labelledby="sequence-heading">
          <div className="mcp-container">
            <div className="mcp-section-head" data-reveal>
              <span className="mcp-eyebrow">How a run goes</span>
              <h2 className="mcp-h2 mcp-h2--wide" id="sequence-heading">
                Discover, match, look, then decide whether to pay.
              </h2>
              <p className="mcp-lead">
                Every step before the file is free — which means your agent can find out whether the
                match is any good before anyone spends anything.
              </p>
            </div>

            <ol className="mcp-card mcp-seq" data-reveal>
              {SEQUENCE.map((step, index) => (
                <li key={step.title} className="mcp-seq__step">
                  <span className="mcp-seq__num" aria-hidden="true">
                    {index + 1}
                  </span>
                  <div className="mcp-seq__body">
                    <h3 className="mcp-h3">{step.title}</h3>
                    <div className="mcp-seq__calls">
                      {step.calls.map((call) => (
                        <code key={call} className="mcp-seq__call">
                          {call}
                        </code>
                      ))}
                    </div>
                    <p className="mcp-body">{step.body}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* ---------- Connect ---------- */}
        <section className="mcp-section mcp-band" aria-labelledby="connect-heading">
          <div className="mcp-container">
            <div className="mcp-section-head" data-reveal>
              <span className="mcp-eyebrow">Connecting</span>
              <h2 className="mcp-h2" id="connect-heading">
                Two steps. Nothing to install.
              </h2>
              <p className="mcp-lead">
                It's a remote server over Streamable HTTP — no stdio wrapper, no npx, no local
                binary. Your client connects to a URL.
              </p>
            </div>

            <div className="mcp-connect__steps">
              <div data-reveal>
                <p className="mcp-step-label">Step 1</p>
                <h3 className="mcp-h3">Get an API key</h3>
                <p className="mcp-body mcp-body--indent">
                  Sign in and create a key at{' '}
                  <Link to={ROUTES.apiKeys} className="mcp-link">
                    redirx.dev/api-keys
                  </Link>
                  . It's shown once, starts with <code>rdx_</code>, and only a hash is stored — copy
                  it when you create it.
                </p>
              </div>

              <div data-reveal style={revealDelay(1)}>
                <p className="mcp-step-label">Step 2</p>
                <h3 className="mcp-h3">Add the server to your client</h3>
                <div className="mcp-terminal">
                  <pre className="mcp-terminal__code">
                    <code ref={connectCodeRef}>{CONNECT_COMMAND}</code>
                  </pre>
                  <CopyCommandButton value={CONNECT_COMMAND} codeRef={connectCodeRef} />
                </div>
                <p className="mcp-meta mcp-meta--caption">
                  Any MCP client that speaks Streamable HTTP works the same way — point it at the
                  URL and set a static <code>Authorization: Bearer</code> header. Check your
                  client's docs for how it sets fixed headers.
                </p>
                <p className="mcp-meta">
                  One-click OAuth sign-in is coming; today you connect with an API key.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ---------- Why quality isn't the paywall ---------- */}
        <section className="mcp-section" aria-labelledby="model-heading">
          <div className="mcp-container">
            <div className="mcp-section-head" data-reveal>
              <span className="mcp-eyebrow">The model</span>
              <h2 className="mcp-h2 mcp-h2--wide" id="model-heading">
                Gating the match quality made sense for humans. It doesn't for agents.
              </h2>
            </div>

            <div className="mcp-model">
              <div className="mcp-prose" data-reveal>
                <p className="mcp-body">
                  Most tools cripple the free tier and hope you upgrade to find out whether it
                  worked. That leverage disappears when the customer is an agent: a degraded run
                  isn't a teaser, it's just bad data your agent will reason from and act on.
                </p>
                <p className="mcp-body">
                  So Redirx inverted it. <code>deep_match</code> runs the same engine, on your full
                  URL set, at full quality, regardless of plan. <code>preview</code> shows you the
                  weak matches on purpose. You decide to pay once you've already seen exactly what
                  you'd be paying for.
                </p>
                <p className="mcp-body">
                  The one thing we ration is volume, not quality: free accounts get a limited number
                  of Deep Match runs in a rolling 24-hour window. Hitting that returns a clear
                  "retry later," never a worse result.
                </p>
              </div>

              <div className="mcp-card" data-reveal style={revealDelay(1)}>
                <table className="mcp-compare">
                  <caption>Where the free line usually falls, and where it falls here.</caption>
                  <thead>
                    <tr>
                      <th scope="col">
                        <span className="mcp-sr-only">Capability</span>
                      </th>
                      <th scope="col" className="mcp-compare__col">
                        Usually free
                      </th>
                      <th scope="col" className="mcp-compare__col">
                        Free here
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {COMPARE_ROWS.map((row) => (
                      <tr key={row.label}>
                        <th scope="row">{row.label}</th>
                        <td className="mcp-compare__col">
                          <Mark included={row.usually} />
                        </td>
                        <td className="mcp-compare__col">
                          <Mark included={row.here} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </section>

        {/* ---------- Payment ---------- */}
        <section className="mcp-section mcp-band" aria-labelledby="payment-heading">
          <div className="mcp-container">
            <div className="mcp-section-head" data-reveal>
              <span className="mcp-eyebrow">Payment</span>
              <h2 className="mcp-h2" id="payment-heading">
                Your agent asks. A human pays.
              </h2>
            </div>

            <div className="mcp-payment" data-reveal>
              <div className="mcp-prose">
                <p className="mcp-body">
                  When <code>export</code> needs payment, it returns a structured Payment Required
                  response — MPP, JSON-RPC error <code>-32042</code> — carrying a checkout URL. That
                  URL has to be opened by a person in a browser. The agent cannot complete the
                  payment, by design: this is Stripe Checkout with a human in the loop, not an
                  autonomous agent-holds-a-card flow.
                </p>
                <p className="mcp-body">
                  After payment, the agent just calls <code>export</code> again. There's no "confirm
                  payment" step to get wrong — the tool re-checks entitlement on every single call,
                  so retrying with the same arguments always works.
                </p>
              </div>

              <div className="mcp-card mcp-panels">
                <div className="mcp-panel">
                  <pre className="mcp-error-block">
                    <code>
                      {'{\n  "error": {\n    '}
                      <span className="mcp-key">"code"</span>
                      {': -32042,\n    '}
                      <span className="mcp-key">"message"</span>
                      {': "Payment Required",\n    "data": {\n      '}
                      <span className="mcp-key">"checkoutUrl"</span>
                      {': "https://checkout…"\n    }\n  }\n}'}
                    </code>
                  </pre>
                </div>
                <div className="mcp-panel">
                  <span className="mcp-glyph" aria-hidden="true">
                    <Lock size={20} strokeWidth={1.75} />
                  </span>
                  <p className="mcp-body">
                    The agent hands you the URL and stops there. Nothing settles until a person
                    completes the checkout.
                  </p>
                  <p className="mcp-meta">Opened by you, in your browser.</p>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ---------- FAQ ---------- */}
        <section className="mcp-section" aria-labelledby="faq-heading">
          <div className="mcp-container mcp-faq">
            <h2 className="mcp-h2 mcp-faq__heading" id="faq-heading" data-reveal>
              Questions worth asking first.
            </h2>
            <div className="mcp-faq__list">
              {FAQ.map((item, index) => (
                <div
                  key={item.question}
                  className="mcp-faq__item"
                  data-reveal
                  style={revealDelay(index)}
                >
                  <h3 className="mcp-faq__q">{item.question}</h3>
                  <p className="mcp-faq__a">{item.answer}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ---------- Closing CTA ---------- */}
        <section className="mcp-section mcp-band" aria-labelledby="closing-heading">
          <div className="mcp-container mcp-closing">
            <h2 className="mcp-h2" id="closing-heading" data-reveal>
              Get a key and point your agent at it.
            </h2>
            <p className="mcp-lead" data-reveal style={revealDelay(1)}>
              Free to connect. Free to discover, match, and preview. Pay when you want the file.
            </p>
            <div data-reveal style={revealDelay(2)}>
              <Link to={ROUTES.apiKeys} className="mcp-btn mcp-btn--primary">
                Get an API key
              </Link>
            </div>
            <p className="mcp-meta mcp-meta--caption" data-reveal style={revealDelay(3)}>
              Built for the way agents actually work: start-then-poll, structured errors, and no
              step an agent can't retry.
            </p>
          </div>
        </section>
      </main>
    </div>
  );
}

function Mark({ included }: { included: boolean }) {
  if (included) {
    return (
      <>
        <Check size={18} strokeWidth={2.25} className="mcp-mark" aria-hidden="true" />
        <span className="mcp-sr-only">Included</span>
      </>
    );
  }
  return (
    <>
      <span className="mcp-dash" aria-hidden="true" />
      <span className="mcp-sr-only">Not included</span>
    </>
  );
}
