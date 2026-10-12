// Сгенерировано из каталога операций mandat-operations 2026.10.1. Не править вручную.

// R-COV-001: Подписание платёжных документов в интернет-банке
permit (principal, action == Mandat::Action::"PAYMENT_SIGN", resource)
when { context.leaf.codes.containsAny(["SYN-BANK-ACCOUNT-DISPOSE", "SYN-BANK-PAY-SIGN"]) };

// R-COV-001: Изменение реквизитов и сведений о клиенте
permit (principal, action == Mandat::Action::"REQUISITES_CHANGE", resource)
when { context.leaf.codes.containsAny(["SYN-BANK-CLIENT-DATA"]) };

// R-COV-001: Просмотр выписок и остатков
permit (principal, action == Mandat::Action::"STATEMENT_VIEW", resource)
when { context.leaf.codes.containsAny(["SYN-BANK-ACCOUNT-DISPOSE", "SYN-BANK-PAY-SIGN", "SYN-BANK-STATEMENT"]) };

// R-COV-001: Управление пользователями интернет-банка
permit (principal, action == Mandat::Action::"USER_ADMIN", resource)
when { context.leaf.codes.containsAny(["SYN-BANK-USER-ADMIN"]) };

// R-TERM-002, R-REG-001: МЧД представителя действует в реестре и в сроке на дату операции.
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
