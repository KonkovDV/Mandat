"""Экспорт ядра правил передоверия в Cedar для независимой проверки.

Cedar (CNCF Sandbox, спецификация проверена в Lean) отвечает только «разрешено/запрещено»
и не знает «на разбор» и «недостаточно данных», поэтому ядром «Мандата» он не становится
(ADR-006). Зато на нём удобно независимо перепроверить самое важное свойство:
передоверие не расширяет полномочия, не продлевает срок и не обходит запрет передоверия.

Экспортируется подмножество правил: покрытие операции (R-COV-001), срок и реестр
(R-TERM-002, R-REG-001), право передоверия (R-SUB-001), сужение (R-SUB-002),
срок передоверия (R-SUB-003), каскад (R-SUB-006). Цепочка — до двух звеньев.
Тест ``tests/test_cedar.py`` сравнивает решения Cedar и «Мандата» на полном переборе.
"""

from __future__ import annotations

from datetime import date

from mandat.rules.classifier import Classifier, default_classifier
from mandat.rules.pack import OperationCatalog, default_catalog

NAMESPACE = "Mandat"

SCHEMA_TEMPLATE = """namespace Mandat {{
  entity Representative;
  entity Organization;
  type Link = {{
    codes: Set<String>,
    registryActive: Bool,
    validFrom: Long,
    validUntil: Long,
    subdelegation: Long,
  }};
  action {actions} appliesTo {{
    principal: Representative,
    resource: Organization,
    context: {{ day: Long, hasParent: Bool, leaf: Link, parent: Link }},
  }};
}}
"""

COMMON_FORBIDS = """// R-TERM-002, R-REG-001: МЧД представителя действует в реестре и в сроке на дату операции.
forbid (principal, action, resource)
unless {
  context.leaf.registryActive &&
  context.leaf.validFrom <= context.day &&
  context.day <= context.leaf.validUntil
};

// R-SUB-001: родительская МЧД без права передоверия (ПрПередов = 1).
forbid (principal, action, resource)
when { context.hasParent && context.parent.subdelegation == 1 };

// R-SUB-002: передоверие не шире родительской МЧД.
forbid (principal, action, resource)
when { context.hasParent && !context.parent.codes.containsAll(context.leaf.codes) };

// R-SUB-003: срок передоверия не дольше срока родительской МЧД.
forbid (principal, action, resource)
when { context.hasParent && context.leaf.validUntil > context.parent.validUntil };

// R-SUB-006: с прекращением основной доверенности теряет силу передоверие.
forbid (principal, action, resource)
when {
  context.hasParent &&
  !(context.parent.registryActive &&
    context.parent.validFrom <= context.day &&
    context.day <= context.parent.validUntil)
};
"""


def day_number(day: date) -> int:
    return day.toordinal()


def export_policies(
    catalog: OperationCatalog | None = None,
    classifier: Classifier | None = None,
    *,
    on: date | None = None,
) -> str:
    """Политики Cedar из каталога операций: коды, исключённые из классификатора на дату, не попадают."""
    catalog = catalog or default_catalog()
    classifier = classifier or default_classifier()
    on = on or date.today()
    blocks = [f"// Сгенерировано из каталога операций {catalog.catalog_id} {catalog.version}. Не править вручную.\n"]
    for op in sorted(catalog.operations.values(), key=lambda o: o.code):
        codes = sorted(
            code for code in op.accepted_codes if (entry := classifier.get(code)) is not None and entry.active_on(on)
        )
        if not codes:
            continue
        listed = ", ".join(f'"{code}"' for code in codes)
        blocks.append(
            f"// R-COV-001: {op.name}\n"
            f'permit (principal, action == {NAMESPACE}::Action::"{op.code}", resource)\n'
            f"when {{ context.leaf.codes.containsAny([{listed}]) }};\n"
        )
    blocks.append(COMMON_FORBIDS)
    return "\n".join(blocks)


def export_schema(catalog: OperationCatalog | None = None) -> str:
    catalog = catalog or default_catalog()
    actions = ", ".join(f'"{code}"' for code in sorted(catalog.operations))
    return SCHEMA_TEMPLATE.format(actions=actions)
