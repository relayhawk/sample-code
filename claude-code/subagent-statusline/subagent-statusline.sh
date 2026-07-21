#!/bin/bash
input=$(cat)

echo "$input" | jq -rc '
  .tasks[]
  | ((.tokenCount // 0) as $t
     | if $t >= 1000 then "\(($t/100|floor)/10)k" else "\($t)" end) as $toks
  | {
      id: .id,
      content: "\(.type) · \(.model // "…") · \(.description // .name) · ↓ \($toks) tokens"
    }
'
