/** Curated, link-checked resources. Each has a ready-made question for "Ask NuevAI". */
export interface Resource {
  title: string;
  desc: string;
  url: string;
  ask?: string;
  badge?: string;
}
export interface ResourceGroup { id: string; title: string; blurb: string; items: Resource[] }

const DOC = (id: string) => `https://docs.google.com/document/d/${id}/edit`;

export const RESOURCE_GROUPS: ResourceGroup[] = [
  {
    id: 'start', title: 'Start here', blurb: 'The documents most questions come back to.',
    items: [
      { title: 'Student & Family Handbook', desc: 'Policies, attendance, honor code, discipline, technology and daily life.', url: DOC('12Ugo4s2AgIWc0A068EibRr0TyGiI6vrf4sW8tSAKZV4'), ask: 'Summarize the key policies in the Student & Family Handbook', badge: 'Handbook' },
      { title: '2026–27 Course Catalog', desc: 'Every Upper School course: prerequisites, terms, grade levels and descriptions.', url: DOC('13_b8FkGjoBhEHjsPZkz6if1pgOiYCHUo_WMNRFpblLU'), ask: 'What courses are offered in the Upper School this year?', badge: 'Academics' },
      { title: 'Upper School Cell Phone Policy', desc: 'Where and when phones may be used during the school day.', url: DOC('15-u90dFy8Q_Hq1CxWpTEjaWbrYVWmN3ZUN82WOZqSgk'), ask: 'What is the cell phone policy?', badge: 'Policy' },
      { title: 'How-To Guide for Students', desc: 'Getting set up and finding your way around Nueva systems.', url: DOC('1J60DzHmv2F4Bkt2oTdAcOZcVqNbNPCFMEfi_0DC2xZI'), ask: 'How do I get started as a new student?', badge: 'Guide' },
    ],
  },
  {
    id: 'student-life', title: 'Clubs & student life', blurb: 'Find a club, start one, or learn the rules.',
    items: [
      { title: 'Clubs & Affinity Groups list', desc: 'Every active club and affinity group with contacts.', url: 'https://docs.google.com/spreadsheets/d/1SbZmf00p0jDMfmfieyQIC59zBmgTKdDvF_ltagJ6bUQ/edit', ask: 'What clubs can I join?', badge: 'Sheet' },
      { title: 'Club Policies', desc: 'What every club must follow.', url: DOC('1PWXs6IC6PV1001ghXKJi20qzgpWZe_m1BJVWNHrjKAo'), ask: 'What are the rules for clubs?' },
      { title: 'Starting a club & club funding', desc: 'Guidelines for creating a club and requesting funds.', url: DOC('1Y1B9MKlW7Cc33NuWMkbkQWHoxWVNBzty01SPmrK9U1o'), ask: 'How do I start a club and get funding?' },
    ],
  },
  {
    id: 'athletics', title: 'Athletics', blurb: 'Teams, schedules and sports medicine.',
    items: [
      { title: 'Upper School Athletics Handbook', desc: 'Eligibility, expectations, transportation and communication.', url: DOC('1qy8kpddkHlIcyIVMIyhCavbRVM8mlbiPX2jZAQEO-mE'), ask: 'What are the Upper School athletics eligibility rules?', badge: 'Handbook' },
      { title: 'Athletics home', desc: 'Programs, teams and news from the Athletics department.', url: 'https://www.nuevaschool.org/school-life/athletics', ask: 'What sports are playing this season?' },
      { title: 'Athletics calendar', desc: 'Official game and event schedule on the school website.', url: 'https://www.nuevaschool.org/school-life/athletics/athletics-calendar', ask: 'When is the next game?' },
      { title: 'Sports medicine', desc: 'Athletic training and injury support.', url: 'https://www.nuevaschool.org/school-life/athletics/sports-medicine' },
    ],
  },
  {
    id: 'academics', title: 'Academics', blurb: 'Programs, counseling and the school website.',
    items: [
      { title: 'Upper School academics', desc: 'Program overview, graduation requirements and more.', url: 'https://www.nuevaschool.org/academics/upper-school', ask: 'What are the Upper School graduation requirements?' },
      { title: 'College counseling', desc: 'Planning, applications and counselors.', url: 'https://www.nuevaschool.org/academics/upper-school/college-counseling', ask: 'How does college counseling work?' },
      { title: 'Faculty & staff directory', desc: 'Find teachers, deans and staff.', url: 'https://www.nuevaschool.org/about/faculty-staff' },
    ],
  },
  {
    id: 'families', title: 'Families & admissions', blurb: 'Visit, register, give and shop.',
    items: [
      { title: 'Visit & apply', desc: 'Admissions steps and dates for every division.', url: 'https://www.nuevaschool.org/admissions/visit-and-apply', ask: 'How do I apply to Nueva?', badge: 'Admissions' },
      { title: 'Register for an admissions event', desc: 'Book tours and info sessions through Ravenna.', url: 'https://www.ravenna-hub.com/schools/20642', ask: 'How do I book a campus tour?', badge: 'Booking' },
      { title: 'Nueva Parents Association', desc: 'Volunteer, events and parent community.', url: 'https://www.nuevaschool.org/school-life/nueva-parents-association' },
      { title: 'Nueva Store', desc: 'Spirit wear and school merchandise.', url: 'https://www.nuevaschool.org/nueva-store' },
      { title: 'Give to Nueva', desc: 'Support financial aid, programs and campus.', url: 'https://www.givecampus.com/campaigns/33909/donations/new', ask: 'How can I donate to Nueva?' },
    ],
  },
  {
    id: 'portals', title: 'Portals & contact', blurb: 'Sign in, calendar, and who to call.',
    items: [
      { title: 'Nexus (MySchoolApp)', desc: 'Grades, assignments and school communications. Needs your Nueva login.', url: 'https://nuevaschool.myschoolapp.com/', badge: 'Sign in' },
      { title: 'Official school calendar', desc: 'Calendar page on the school website.', url: 'https://www.nuevaschool.org/about/news-events/calendar' },
      { title: 'Contact the school', desc: 'Phone numbers and offices for both campuses.', url: 'https://www.nuevaschool.org/contact-us', ask: 'Who do I contact for help with my schedule?' },
    ],
  },
];
