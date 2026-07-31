// AncientMedKG Neo4j 5.x constraints and indexes.
CREATE CONSTRAINT entity_node_id IF NOT EXISTS
FOR (node:Entity) REQUIRE node.node_id IS UNIQUE;

CREATE CONSTRAINT source_document_id IF NOT EXISTS
FOR (node:SourceDocument) REQUIRE node.source_id IS UNIQUE;

CREATE CONSTRAINT evidence_span_id IF NOT EXISTS
FOR (node:EvidenceSpan) REQUIRE node.evidence_id IS UNIQUE;

CREATE CONSTRAINT assertion_id IF NOT EXISTS
FOR (node:Assertion) REQUIRE node.assertion_id IS UNIQUE;

CREATE INDEX entity_canonical_name IF NOT EXISTS
FOR (node:Entity) ON (node.canonical_name);

CREATE INDEX entity_type IF NOT EXISTS
FOR (node:Entity) ON (node.entity_type);

CREATE INDEX source_doi IF NOT EXISTS
FOR (node:SourceDocument) ON (node.doi);

CREATE INDEX evidence_quote_sha256 IF NOT EXISTS
FOR (node:EvidenceSpan) ON (node.quote_sha256);
