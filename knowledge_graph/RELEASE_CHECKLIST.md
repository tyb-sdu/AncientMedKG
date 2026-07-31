# 发布检查单

- [ ] 输入证据包有唯一 `graph_version` 和明确 `parent_version`
- [ ] 古籍来源记录包含 PDF SHA-256、`book_id`、`page_id` 和物理页
- [ ] 现代文献来源记录包含 PDF SHA-256、`doc_id`、DOI、`chunk_id` 和 PDF 页
- [ ] 同名方剂已按组成与来源拆分为 `FormulaVariant`
- [ ] 直接古籍证据、现代桥接、计算预测和专家假设没有混级
- [ ] `python -m knowledge_graph build ... --release` 通过
- [ ] `python -m knowledge_graph verify-sources ...` 返回 `valid=true`
- [ ] `python -m knowledge_graph validate ... --release` 返回 `valid=true`
- [ ] `python -m knowledge_graph doctor ...` 返回 `valid=true`
- [ ] Neo4j 导出 manifest 的计数与 SHA-256 已核对
- [ ] 关键路径可追溯率为 1.0，普通关系可追溯率不低于 0.98
- [ ] 私有 PDF、引文数据包、数据库和索引未进入 Git 暂存区
- [ ] 公开仓库测试通过，提交 SHA 和远端 `main` 一致
