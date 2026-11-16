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
    screens: [{ id: "explore", label: "Explore" }],
  },
  {
    index: 3,
    label: "Hypothesize",
    question: "Why these results? What would show we're wrong?",
    screens: [{ id: "hypothesize", label: "Hypothesize" }],
  },
  {
    index: 4,
    label: "Design",
    question: "What configurations would isolate the hypothesized cause?",
    screens: [{ id: "test", label: "Test" }],
  },
  { index: 5, label: "Run", question: null, screens: [{ id: "test", label: "Test" }] },
  {
    index: 6,
    label: "Compare",
    question: "Do observations match predictions? What explains results?",
    screens: [{ id: "result", label: "Result" }],
  },
];

export const LOOP_FIRST = 3;

export const DECISION = {
  id: "decision",
  label: "Decision",
  question: "Do we understand enough to make a configuration decision?",
};

export function stepsOf(screenId: string): Step[] {
  return STEPS.filter((step) => step.screens.some((screen) => screen.id === screenId));
}
