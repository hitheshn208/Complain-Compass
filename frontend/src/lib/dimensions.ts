/**
 * Customer dimensions shown in the UI (filter bar, Segments page, issue breakdowns).
 * The backend supports all five: country, state, age_group, student_status, income_bracket.
 * To show State or Student status again, add them back to this list.
 */
export const UI_DIMENSIONS = [
  { key: "country", label: "Country" },
  { key: "age_group", label: "Age group" },
  { key: "income_bracket", label: "Income" },
] as const;

export type UiDimension = (typeof UI_DIMENSIONS)[number]["key"];
