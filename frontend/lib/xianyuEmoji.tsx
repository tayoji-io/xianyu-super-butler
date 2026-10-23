import React from 'react';

import { renderRichText } from './emojiMap';
import { XIANYU_EMOJI_MAP } from './xianyuEmojiData';

// 把 [表情码] 拆出来：闲鱼自定义表情渲染成图片，其它交给 emojiMap 的 Unicode 兜底。
const TOKEN_PATTERN = /(\[[^\[\]]{1,12}\])/g;

export function renderMessageContent(text: string): React.ReactNode {
  if (!text) return text;

  return text.split(TOKEN_PATTERN).map((part, index) => {
    const emoji = XIANYU_EMOJI_MAP[part];
    if (emoji) {
      return (
        <img
          key={`emoji-${index}`}
          src={emoji.url}
          alt={emoji.name}
          title={emoji.code}
          loading="lazy"
          referrerPolicy="no-referrer"
          draggable={false}
          className="mx-0.5 inline-block h-6 w-6 align-text-bottom"
        />
      );
    }
    return <React.Fragment key={`text-${index}`}>{renderRichText(part)}</React.Fragment>;
  });
}
