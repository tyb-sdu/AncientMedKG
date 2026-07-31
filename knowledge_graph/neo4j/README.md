# Neo4j 导出说明

`python -m knowledge_graph export-neo4j` 会将本目录中的 Cypher 文件复制到版本化导出目录，并生成：

- `nodes.csv`：五层和扩展实体；
- `source_nodes.csv`：原始文献、古籍版本和数据库来源；
- `evidence_nodes.csv`：页码、引文、哈希和证据等级；
- `assertion_nodes.csv`：关系的可追溯重建；
- `relationships.csv`：用于高效查询的直接关系；
- `provenance_relationships.csv`：断言—证据—来源链；
- `graph.jsonld`：跨系统交换格式；
- `neo4j_import_manifest.json`：计数、SHA-256 和内容指纹。

导入前必须先运行知识图谱 release 验证和 SQLite 来源反查。Neo4j 只是查询与分析副本，不能成为原始证据的唯一保存位置。
