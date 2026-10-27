// ProjectCompass adapter. The imported WorldModel is upstream Apache-2.0 code.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const { WorldModel, canonicalJson, externalId } = await import(pathToFileURL(path.join(process.argv[2], 'src/index.ts')).href);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const currentSources = new Set(input.sources.map(s => s.id));
const allowedSupport = new Map();
for (const fact of input.facts) {
  const ids = new Set([...fact.evidence_ids, ...input.relations
    .filter(r => r.fact_id === fact.id && r.status === 'confirmed').map(r => r.evidence_id)]);
  if ([...ids].some(id => !currentSources.has(id))) throw new Error('Missing current supporting evidence');
  allowedSupport.set(fact.id, ids);
}
const world = new WorldModel({ storage: 'local', namespace: input.namespace, directory: input.directory });
const graph = world.view('graph');
const system = 'projectcompass';
const key = (type, id) => externalId(type, system, id);
const provenance = (item, kind = 'agent') => ({ kind, by: item.author ?? item.reviewer ?? 'projectcompass',
  at: item.observed_at, ...(item.source_ref ? { url: item.source_ref } : {}) });
const knownContexts = new Set(world.events().filter(e => e.kind === 'upsertContext').map(e => e.payload.id));

async function entity(type, id, attrs, source) {
  const existing = graph.entity(key(type, id));
  if (!existing || canonicalJson(existing.attrs) !== canonicalJson(attrs) || canonicalJson(existing.source) !== canonicalJson(source)) {
    await world.upsertEntity({ type, externalKey: { system, id }, attrs, source });
  }
  return key(type, id);
}

// A context per pair avoids World Model's same-predicate temporal replacement
// closing another supporting-source edge. No automatic assertFact is used.
async function edge(from, type, to, source) {
  const id = createHash('sha256').update(`${from}\0${type}\0${to}`).digest('hex');
  const context = key('SupportContext', id);
  if (!knownContexts.has(context)) {
    await world.upsertContext({ type: 'SupportContext', externalKey: { system, id }, attrs: {}, source });
    knownContexts.add(context);
  }
  await world.relate({ from, type, to, context, source, validFrom: source.at });
}

for (const source of input.sources) {
  await entity('Evidence', source.id, source, provenance(source, 'file'));
}
const accepted = new Map();
for (const review of input.reviews) {
  if (review.action !== 'accept_fact' || !review.fact_id) continue;
  accepted.set(review.subject_key ?? review.fact_id, review);
  const id = createHash('sha256').update(canonicalJson(review)).digest('hex');
  await entity('OwnerReview', id, review, provenance(review, 'manual'));
}
const wanted = new Set(input.facts.map(f => key('Claim', f.id)));
for (const fact of input.facts) {
  const review = accepted.get(fact.subject_key);
  const isAccepted = review?.fact_id === fact.id;
  const attrs = { ...fact, status: isAccepted ? 'accepted' : fact.status };
  const id = await entity('Claim', fact.id, attrs, review ? provenance(review, 'manual') : provenance(fact));
  for (const sourceId of fact.evidence_ids) {
    if (!graph.entity(key('Evidence', sourceId))) throw new Error('Missing cited evidence');
    await edge(id, 'supported_by', key('Evidence', sourceId), provenance(fact));
  }
  for (const relation of input.relations.filter(r => r.fact_id === fact.id && r.status === 'confirmed')) {
    if (!graph.entity(key('Evidence', relation.evidence_id))) throw new Error('Missing confirmed evidence');
    await edge(id, 'supported_by', key('Evidence', relation.evidence_id), provenance(fact));
  }
}
// Retrieval genuinely comes from the upstream materialized graph. Old entities
// stay in history, while only the current canonical input belongs in this view.
const entities = graph.query({ type: 'Claim' }).filter(e => wanted.has(e.id));
const facts = entities.map(e => e.attrs);
const supportByFact = Object.fromEntries(entities.map(e => [e.attrs.id,
  [...new Set(graph.neighborsOf(e.id, { via: 'supported_by' })
    .filter(s => s.type === 'Evidence' && allowedSupport.get(e.attrs.id).has(s.attrs.id))
    .map(s => s.attrs.id))]]));
console.log(JSON.stringify({ facts, support_by_fact: supportByFact, status: { fact_count: facts.length,
  accepted_count: facts.filter(f => f.status === 'accepted').length,
  support_count: Object.values(supportByFact).reduce((n, ids) => n + ids.length, 0),
  review_count: graph.query({ type: 'OwnerReview' }).length,
  event_count: world.events().length, audit_count: world.audit().length,
  queries: ['graph.query(Claim)', 'graph.neighborsOf(supported_by)', 'graph.query(OwnerReview)', 'events()', 'audit()'] } }));
