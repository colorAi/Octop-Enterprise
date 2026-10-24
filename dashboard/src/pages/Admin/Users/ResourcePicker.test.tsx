import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SkillChoices } from "./ResourcePicker";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);
const skills = [
  {
    id: "workspace:1:pdf",
    slug: "pdf",
    name: "PDF",
    description: "Documents",
    category: "other",
    source_name: "Expert",
    kind: "workspace",
    expert_ids: ["expert"],
  },
  {
    id: "capability:generate_image",
    slug: "company-image-generation",
    name: "公司生图",
    description: "Generate images",
    category: "company-capabilities",
    source_name: "company",
    kind: "capability",
    expert_ids: [],
  },
  {
    id: "capability:generate_video",
    slug: "company-video-generation",
    name: "公司生视频",
    description: "Generate videos",
    category: "company-capabilities",
    source_name: "company",
    kind: "capability",
    expert_ids: [],
  },
];
it("filters company capabilities and selects only the visible category", () => {
  const select = vi.fn();
  render(<SkillChoices skills={skills} selected={[]} setSelected={select} />);
  expect(screen.getAllByRole("checkbox")).toHaveLength(3);
  fireEvent.click(
    screen.getByRole("button", {
      name: "employeeResources.companyCapabilities",
    }),
  );
  expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  expect(screen.queryByRole("checkbox", { name: "PDF" })).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.selectAll" }),
  );
  expect(select).toHaveBeenCalledWith([
    "capability:generate_image",
    "capability:generate_video",
  ]);
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.otherCategory" }),
  );
  expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  expect(screen.getByRole("checkbox", { name: "PDF" })).toBeTruthy();
});
it("does not duplicate the all filter when every skill has the same category", () => {
  render(
    <SkillChoices
      skills={skills.slice(0, 1)}
      selected={[]}
      setSelected={vi.fn()}
    />,
  );
  expect(
    screen.queryByRole("button", { name: "employeeResources.otherCategory" }),
  ).toBeNull();
  expect(screen.getByRole("checkbox", { name: "PDF" })).toBeTruthy();
});
