type CategoryReference = {
  id: string
  name: string
}

type CategoryDisplayReference = {
  category_id: string
  category_name: string
  category_icon: string
  category_color: string
}

type RuleReference = {
  actions: Array<{ op: string; value: string }>
}

export function findCategoryReference<T extends CategoryReference>(
  categories: readonly T[],
  categoryId: string,
): T | undefined {
  return categories.find((category) => category.id === categoryId)
}

export function findBudgetCategoryDisplay<T extends CategoryDisplayReference>(
  categories: readonly T[],
  categoryId: string,
): T | undefined {
  return categories.find((category) => category.category_id === categoryId)
}

export function getRuleCategoryId(rule: RuleReference): string | null {
  return rule.actions.find((action) => action.op === 'set_category' && action.value)?.value ?? null
}

export function getRuleCategoryName<T extends CategoryReference>(
  rule: RuleReference,
  categories: readonly T[],
): string | null {
  const categoryId = getRuleCategoryId(rule)
  if (!categoryId) return null
  return findCategoryReference(categories, categoryId)?.name ?? null
}
