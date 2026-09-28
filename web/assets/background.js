/* background.js — where an import came from, as a horse's page shows it.
 *
 * From HKJC's "Intro to New Horses" (query/background), shown for a horse's
 * first five Hong Kong starts on the Briefing and Race Day: who trained and
 * owned it before, the agent, the sale, how it trialled or raced, and the
 * profile's own words. The agent also rides on the runner's row as a chip,
 * so a Price Bloodstock horse is seen without opening it.
 */
import { el } from './vocab.js';

const IMPORT = { PP: 'PP · raced before import', PPG: 'PPG · unraced import',
                 ISG: 'ISG · HKJC sale griffin' };

/** "Price Bloodstock" -> "PRICE": what fits on a chip. */
export const agentShort = (a) => a.replace(/\s+(Bloodstock|Equine)$/i, '').toUpperCase();

function sale(b) {
  if (!b.sale_amount) return '';
  const n = Math.round(b.sale_amount).toLocaleString('en-US');
  const price = b.sale_ccy === 'GNS' ? `${n} gns` : `${b.sale_ccy} ${n}`;
  const aud = b.sale_ccy !== 'AUD' && b.sale_aud ? ` (~A$${Math.round(b.sale_aud / 1000)}k)` : '';
  return `${b.sale_kind || 'sold'} at ${price}${aud}`;
}

/** The runner's background, as the block reads it. `starts` is the number
 *  of Hong Kong starts it has made before this race. */
export function backgroundView(b, starts) {
  const facts = [IMPORT[b.import_type] || b.import_type];
  if (b.origin) facts.push(`foaled in ${b.origin}`);
  if (b.overseas_starts !== null && b.overseas_starts !== undefined) {
    facts.push(`${b.overseas_wins ?? 0} win${b.overseas_wins === 1 ? '' : 's'} `
      + `from ${b.overseas_starts} overseas`);
  }
  if (b.trial_won !== null && b.trial_won !== undefined) {
    facts.push(b.trial_won ? 'won a trial or jump-out' : 'trialled, did not win');
  }
  const s = sale(b);
  if (s) facts.push(s);
  if (b.sire) {
    facts.push(`by ${b.sire}${b.sire_hk_starters
      ? ` (${b.sire_hk_winners} of ${b.sire_hk_starters} in HK won)` : ''}`);
  }
  const who = [];
  if (b.prev_trainer) who.push(`ex ${b.prev_trainer}${b.prev_country ? ` (${b.prev_country})` : ''}`);
  else if (b.prev_country) who.push(`trained in ${b.prev_country}`);
  if (b.prev_owner) who.push(`owned by ${b.prev_owner}`);
  if (b.buyer) who.push(`bought by ${b.buyer}`);
  if (b.prev_name) who.push(`raced as ${b.prev_name}`);
  return {
    head: `BACKGROUND · HK START ${starts + 1}`,
    facts: facts.join(' · '), who: who.join(' · '),
    agents: b.agents || [], url: b.url, about: b.about || '',
  };
}

/** In a runner's detail, under the Screen's reasons. */
export function backgroundBlock(v, { compact = false } = {}) {
  const box = el('div', 'bf-bg');
  const hd = el('div', 'hd');
  hd.append(el('span', 'cap', v.head));
  v.agents.forEach((a) => hd.append(el('span', 'bf-agent', a)));
  const go = el('a', 'go', 'profile ▸');
  go.href = v.url;
  go.target = '_blank';
  go.rel = 'noopener';
  go.addEventListener('click', (e) => e.stopPropagation());
  hd.append(go);
  box.append(hd, el('div', 'facts', v.facts));
  if (v.who) box.append(el('div', 'who', v.who));
  if (v.about && !compact) {
    // The profile's own words, one line until clicked: what the rules could
    // not read is still here to be read.
    const about = el('div', 'about', v.about);
    about.title = 'HKJC Intro to New Horses (written by its contractor) — click to read';
    about.addEventListener('click', (e) => { e.stopPropagation(); about.classList.toggle('open'); });
    box.append(about);
  }
  return box;
}
