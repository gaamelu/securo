import { describe, expect, it } from 'vitest'

import {
  findBudgetCategoryDisplay,
  findCategoryReference,
  getRuleCategoryId,
  getRuleCategoryName,
} from './category-reference-utils'

const hiddenCategory = {
  id: 'hidden-category',
  name: 'Historic category',
  is_hidden: true,
}

const rule = {
  actions: [{ op: 'set_category', value: hiddenCategory.id }],
}

describe('category reference resolution', () => {
  it('resolves hidden categories when the full display catalog supplies them', () => {
    expect(findCategoryReference([hiddenCategory], hiddenCategory.id)).toBe(hiddenCategory)
    expect(getRuleCategoryId(rule)).toBe(hiddenCategory.id)
    expect(getRuleCategoryName(rule, [hiddenCategory])).toBe(hiddenCategory.name)
  })

  it('does not invent a label for a missing category reference', () => {
    expect(getRuleCategoryName(rule, [])).toBeNull()
  })

  it('resolves embedded budget comparison metadata', () => {
    const progress = {
      category_id: hiddenCategory.id,
      category_name: hiddenCategory.name,
      category_icon: 'archive',
      category_color: '#64748b',
    }

    expect(findBudgetCategoryDisplay([progress], hiddenCategory.id)).toBe(progress)
  })
})
