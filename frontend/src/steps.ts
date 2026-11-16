export type Screen = { id: string; label: string };

export type Step = {
  index: number;
  label: string;
  question: string | null;
  screens: Screen[];
};

export const STEPS: Step[] = [
  {
    index: 1,
    label: "Characterize",
    question: "What are we studying? How should we measure it?",
    screens: [
      { id: "workload", label: "Workload" },
      { id: "configurations", label: "Configurations" },
    ],
  },
  {
    index: 2,
    label: "Explore",
    question: "Which interactions suggest dominant bottlenecks or unexpected results?",
    screens: [],
  },
  {
    index: 3,
    label: "Hypothesize",
    question: "Why these results? What would show we're wrong?",
    screens: [],
  },
  {
    index: 4,
    label: "Design",
    question: "What configurations would isolate the hypothesized cause?",
    screens: [],
  },
  { index: 5, label: "Run", question: null, screens: [] },
  {
    index: 6,
    label: "Compare",
    question: "Do observations match predictions? What explains results?",
    screens: [],
  },
];

export const LOOP_FIRST = 3;

export function stepOf(screenId: string): Step | undefined {
  return STEPS.find((step) => step.screens.some((screen) => screen.id === screenId));
}
