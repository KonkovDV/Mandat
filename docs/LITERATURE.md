# Научные основания

Научные работы, на которые опираются решения «Мандата», и что из каждой следует для кода. Библиографию всех работ сверили 13.10.2026 по Crossref, arXiv, USENIX, RFC Editor и W3C.

## Делегирование и авторизация

| Работа | Вывод для «Мандата» | Где в коде |
|---|---|---|
| Li, Grosof, Feigenbaum. *Delegation Logic: A Logic-based Approach to Distributed Authorization.* ACM TISSEC 6(1), 2003 | Делегирование с ограничением глубины формализуется — прямой аналог `ПрПередов` (1/2/3) | `R-SUB-001`, `domain/graph.py` |
| Becker, Fournet, Gordon. *SecPAL: Design and Semantics of a Decentralized Authorization Language.* Journal of Computer Security 18(4), 2010 | Делегирование с ограничениями и доказательством вывода: решение сопровождается тем, из чего оно выведено | находки со ссылками на источник, `Finding` |
| Gurevich, Neeman. *DKAL: Distributed-Knowledge Authorization Language.* IEEE CSF, 2008 | Доверенность — утверждение того, кто вправе его делать; проверка — вывод из утверждений с проверкой права говорящего | `R-EIO-001`: корневую МЧД подписал руководитель на дату выдачи |
| Birgisson et al. *Macaroons: Cookies with Contextual Caveats for Decentralized Authorization in the Cloud.* NDSS, 2014 | Аттенуация: производное право может только сужаться — то же, что сужение при передоверии | `R-SUB-002`, `R-SUB-003`, `tests/test_properties.py` |
| Pang et al. *Zanzibar: Google's Consistent, Global Authorization System.* USENIX ATC, 2019 | Граф отношений в масштабе, но без истории на дату и без доказательной базы | обоснование ADR-006 |
| Cutler et al. *Cedar: A New Language for Expressive, Fast, Safe, and Analyzable Authorization.* PACMPL 8 (OOPSLA1), 2024, DOI 10.1145/3649835 | Узкий язык политик с формальной семантикой позволяет доказывать свойства | `rules/cedar_export.py` |
| Disselkoen et al. *How We Built Cedar: A Verification-Guided Approach.* arXiv:2407.01688, 2024 | Проверка, ведомая спецификацией: при доказательствах нашли и исправили ошибки валидатора; дифференциальное тестирование реализации против модели | `tests/test_cedar.py`: «Мандат» против Cedar на 384 цепочках |

## Право как код

| Работа | Вывод | Где в коде |
|---|---|---|
| Merigoux, Chataing, Protzenko. *Catala: A Programming Language for the Law.* ICFP (PACMPL 5), 2021 | Каждое вычисление привязано к статье закона | `norm_ref` у каждого правила, `docs/RULES.md` |
| OECD OPSI. *Cracking the Code: Rulemaking for Humans and Machines.* OECD Working Papers on Public Governance, 12.10.2020 | Rules as Code: норма и её машинная версия ведутся вместе, с ответственным за актуальность | правила-данные со статусом `draft`/`approved` |

## Языковые модели в юридических задачах

| Работа | Вывод | Где в коде |
|---|---|---|
| Magesh, Surani, Dahl, Suzgun et al. *Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools.* Journal of Empirical Legal Studies 22(2), 2025 (arXiv:2405.20362) | Коммерческие юридические системы с поиском по источникам тоже ошибаются | модель не выносит вердикт (ADR-001) |
| Dahl, Magesh, Suzgun, Ho. *Large Legal Fictions: Profiling Legal Hallucinations in Large Language Models.* Journal of Legal Analysis 16(1), 2024 | Высокая частота юридических галлюцинаций у моделей общего назначения | шлюз дословных цитат, `advisory/gate.py` |
| Guha et al. *LegalBench.* NeurIPS Datasets and Benchmarks, 2023 | Методика юридических задач для моделей: разбивать на проверяемые подзадачи | извлечение фактов устава по видам, а не «оцени устав» |
| Fenogenova et al. *MERA: A Comprehensive LLM Evaluation in Russian.* ACL, 2024 | Русскоязычные бенчмарки для выбора модели | выбор on-prem модели перед пилотом |
| Greshake et al. *Not What You've Signed Up For: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection.* AISec, 2023 | Инструкции, спрятанные в документе, перехватывают модель | документ в маркерах данных, модель изолирована от вердикта, `FORBIDDEN_LLM_ACTIONS` |

## Оценка и статистика

| Работа | Вывод | Где в коде |
|---|---|---|
| Geifman, El-Yaniv. *Selective Classification for Deep Neural Networks.* NeurIPS, 2017 (arXiv:1705.08500) | Система с отказом оценивается парой «риск — покрытие» | `coverage`, `auto_risk` в `eval/run.py` |
| Angelopoulos, Bates. *A Gentle Introduction to Conformal Prediction and Distribution-Free Uncertainty Quantification.* arXiv:2107.07511 | Гарантии покрытия при отказе — когда появится вероятностная часть | план спринта 2 для модели |
| Brown, Cai, DasGupta. *Interval Estimation for a Binomial Proportion.* Statistical Science 16(2), 2001 | При малой выборке интервал Вилсона корректнее нормального | `eval/metrics.wilson_interval` |
| Artstein, Poesio. *Inter-Coder Agreement for Computational Linguistics.* Computational Linguistics 34(4), 2008 | Согласие разметчиков — через κ, а не через долю совпадений | `eval/metrics.cohen_kappa` |

## Журналы и прозрачность

| Работа | Вывод | Где в коде |
|---|---|---|
| Laurie, Langley, Kasper. *Certificate Transparency.* RFC 6962, 2013; Laurie, Messeri, Stradling. *Certificate Transparency Version 2.0.* RFC 9162, 2021 | Дерево Меркла и короткое доказательство включения записи | `audit/merkle.py`, `mandat journal-proof` |
| W3C. *Bitstring Status List v1.0.* Рекомендация, 2025 | Статус удостоверения проверяется без запроса к издателю, номера выдаются случайно ради приватности | `audit/status_list.py` |
