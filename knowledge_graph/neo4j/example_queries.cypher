// Trace a complete ancient-work-to-herb path with page evidence.
MATCH path =
  (work:ClassicWork)-[:HAS_EDITION]->(:Edition)-[:HAS_PASSAGE]->(passage:Passage)
  <-[:RECORDED_IN]-(disease:Disease)-[:HAS_TREATMENT_METHOD]->(method:TreatmentMethod)
  -[:REPRESENTATIVE_FORMULA]->(formula:FormulaVariant)-[:HAS_INGREDIENT]->(herb:Herb)
RETURN work.canonical_name, passage.canonical_name, disease.canonical_name,
       method.canonical_name, formula.canonical_name, herb.canonical_name
LIMIT 50;

// Keep same-name formula variants separate and compare composition/source locators.
MATCH (variant:FormulaVariant)-[:VARIANT_OF]->(concept:FormulaConcept)
WITH concept, collect({
  id: variant.node_id,
  name: variant.canonical_name,
  attributes: variant.attributes_json
}) AS variants
WHERE size(variants) > 1
RETURN concept.canonical_name, variants;

// Resolve a direct graph relationship through its reified assertion and evidence.
MATCH (assertion:Assertion)-[:ASSERTS_ENTITY {role: 'subject'}]->(subject:Entity),
      (assertion)-[:ASSERTS_ENTITY {role: 'object'}]->(object:Entity),
      (assertion)-[:SUPPORTED_BY]->(evidence:EvidenceSpan)
      -[:EXTRACTED_FROM]->(source:SourceDocument)
RETURN assertion.predicate, subject.canonical_name, object.canonical_name,
       evidence.evidence_grade, evidence.locator_json, evidence.quote,
       source.title, source.file_sha256
LIMIT 100;

// Inspect mechanism-transfer hypotheses separately from direct ancient evidence.
MATCH (source:Entity)-[relation:MECHANISM_TRANSFER]->(target:Entity)
RETURN source.canonical_name, target.canonical_name,
       relation.evidence_grade, relation.assertion_mode,
       relation.confidence, relation.evidence_ids_json
ORDER BY relation.confidence DESC;

// Follow the modern herb-compound-target-pathway extension.
MATCH path = (herb:Herb)-[:CONTAINS_COMPOUND]->(compound:Compound)
  -[:TARGETS]->(target:Target)-[:PARTICIPATES_IN]->(pathway:Pathway)
RETURN herb.canonical_name, compound.canonical_name,
       target.canonical_name, pathway.canonical_name
LIMIT 100;
