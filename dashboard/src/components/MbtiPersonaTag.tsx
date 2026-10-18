import { Tag, Tooltip } from "antd";
import { useTranslation } from "react-i18next";

interface MbtiPersonaTagProps {
  value: string | null | undefined;
  /** Show a placeholder tag when MBTI is not set. */
  showDefault?: boolean;
  /** Click handler — when provided, the tag becomes clickable. */
  onClick?: () => void;
}

const MBTI_ZH_NAMES: Record<string, string> = {
  INTJ: "建筑师",
  INTP: "逻辑学家",
  ENTJ: "指挥官",
  ENTP: "辩论家",
  INFJ: "提倡者",
  INFP: "调停者",
  ENFJ: "主人公",
  ENFP: "竞选者",
  ISTJ: "物流师",
  ISFJ: "守卫者",
  ESTJ: "总经理",
  ESFJ: "执政官",
  ISTP: "鉴赏家",
  ISFP: "探险家",
  ESTP: "企业家",
  ESFP: "表演者",
};

const MBTI_EN_NAMES: Record<string, string> = {
  INTJ: "Architect",
  INTP: "Logician",
  ENTJ: "Commander",
  ENTP: "Debater",
  INFJ: "Advocate",
  INFP: "Mediator",
  ENFJ: "Protagonist",
  ENFP: "Campaigner",
  ISTJ: "Logistician",
  ISFJ: "Defender",
  ESTJ: "Executive",
  ESFJ: "Consul",
  ISTP: "Virtuoso",
  ISFP: "Adventurer",
  ESTP: "Entrepreneur",
  ESFP: "Entertainer",
};

export default function MbtiPersonaTag({
  value,
  showDefault = true,
  onClick,
}: MbtiPersonaTagProps) {
  const { t, i18n } = useTranslation();

  if (!value && !showDefault) return null;

  const code = value?.toUpperCase() ?? "";
  const localizedName =
    code &&
    (i18n.language === "zh" ? MBTI_ZH_NAMES[code] : MBTI_EN_NAMES[code]);
  const label = code
    ? localizedName
      ? `${code} ${localizedName}`
      : code
    : t("experts.mbtiDefault");
  const clickable = !!onClick;

  const tag = (
    <Tag
      color={value ? "purple" : "default"}
      style={{
        margin: 0,
        cursor: clickable ? "pointer" : undefined,
      }}
      onClick={
        clickable
          ? (e) => {
              e.stopPropagation();
              onClick();
            }
          : undefined
      }
    >
      {label}
    </Tag>
  );

  return (
    <>
      {clickable ? (
        <Tooltip title={t("experts.mbtiViewDetail")}>{tag}</Tooltip>
      ) : (
        <Tooltip title={t("nav.mbti")}>{tag}</Tooltip>
      )}
    </>
  );
}
