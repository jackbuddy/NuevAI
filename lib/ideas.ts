/** Real questions the assistant answers well, grouped for the "What can I ask?" sheet. Policy, courses and breaks live here rather than on the home screen. */
export interface IdeaGroup { id: string; label: string; prompts: string[] }

export const IDEAS: IdeaGroup[] = [
  { id: 'today', label: 'Today', prompts: ['What’s for lunch today?', 'What games are today?', 'Is there school tomorrow?', 'What kind of day is it today?', 'What’s happening on campus this week?'] },
  { id: 'sports', label: 'Sports', prompts: ['What games are this week?', 'When is the next home game?', 'What’s the Varsity Boys Basketball schedule?', 'When does girls basketball play at home?', 'Are there any away games this week?'] },
  { id: 'clubs', label: 'Clubs', prompts: ['What clubs meet today?', 'What clubs meet at lunch?', 'How do I start a club?', 'Which clubs are about coding or robotics?'] },
  { id: 'lunch', label: 'Lunch', prompts: ['What’s for lunch tomorrow?', 'What’s for lunch at San Mateo?', 'What’s for lunch at Hillsborough?', 'What’s for lunch on Friday?'] },
  { id: 'calendar', label: 'Calendar & days', prompts: ['When is the next no-school day?', 'When is Thanksgiving break?', 'When is winter break?', 'Is tomorrow an A day or a B day?', "What does an A' day mean?", 'When are the next PD days?'] },
  { id: 'handbook', label: 'Handbook & policies', prompts: ['What’s the dress code?', 'What’s the attendance policy?', 'What’s the phone policy?', 'What are the graduation requirements?'] },
  { id: 'resources', label: 'Resources', prompts: ['How do I book a campus tour?', 'Where can I find the student handbook?', 'Where do I find course offerings?'] },
];

/** Real example questions that rotate in the empty composer. Short enough for a phone. */
export const PLACEHOLDERS = [
  'Ask about lunch, games, clubs…',
  'What’s for lunch tomorrow?',
  'Is there a home game this week?',
  'Which clubs meet today?',
  'When is the next no-school day?',
  'What’s the dress code?',
  'How do I book a campus tour?',
];
