import { describe, expect, it } from 'vitest';
import { escapeHtml, renderMarkdown } from './markdown';

describe('renderMarkdown', () => {
  it('escapes HTML everywhere', () => {
    const html = renderMarkdown('<img src=x onerror=alert(1)> **<b>x</b>** `<i>` \n```\n<script>alert(1)</script>\n```');
    expect(html).not.toMatch(/<img|<script|<b>|<i>/);
    expect(html).toContain('&lt;img');
    expect(html).toContain('&lt;script&gt;');
  });

  it('escapes quotes so attributes cannot be broken out of', () => {
    expect(escapeHtml(`"'<>&`)).toBe('&quot;&#39;&lt;&gt;&amp;');
    const html = renderMarkdown('[x](https://a.example/"onmouseover="alert(1))');
    expect(html).not.toContain('" onmouseover');
    expect(html).not.toMatch(/href="[^"]*"onmouseover/);
  });

  it('drops unsafe link targets', () => {
    expect(renderMarkdown('[x](javascript:alert(1))')).not.toContain('href');
    expect(renderMarkdown('[x](data:text/html,hi)')).not.toContain('href');
    expect(renderMarkdown('[x](https://a.b/c?d=1&e=2)')).toContain('href="https://a.b/c?d=1&amp;e=2"');
  });

  it('renders headings, emphasis, code and lists', () => {
    const html = renderMarkdown('# Title\n\nSome **bold**, *it* and `code_x`.\n\n- a\n- b\n  - c\n\n1. one\n2. two');
    expect(html).toContain('<h1>Title</h1>');
    expect(html).toContain('<strong>bold</strong>');
    expect(html).toContain('<em>it</em>');
    expect(html).toContain('<code>code_x</code>');
    expect(html).toContain('<ul><li>a</li><li>b<ul><li>c</li></ul></li></ul>');
    expect(html).toContain('<ol><li>one</li><li>two</li></ol>');
  });

  it('leaves snake_case alone and keeps line breaks', () => {
    expect(renderMarkdown('email_send and task_id')).toBe('<p>email_send and task_id</p>');
    expect(renderMarkdown('a\nb')).toBe('<p>a<br>b</p>');
  });

  it('renders tables and fenced code', () => {
    expect(renderMarkdown('| A | B |\n|---|--:|\n| a | `m` |')).toContain('<td style="text-align:right"><code>m</code></td>');
    expect(renderMarkdown('```json\n{"a": 1}\n```')).toBe('<pre><code class="lang-json">{&quot;a&quot;: 1}</code></pre>');
  });

  it('returns empty for empty input', () => {
    expect(renderMarkdown('')).toBe('');
  });
});
