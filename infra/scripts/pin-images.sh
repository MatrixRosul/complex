#!/usr/bin/env bash
# =============================================================================
# pin-images.sh — резолвить теги сторонніх образів у дайджести і вписує їх
# у infra/images.env.
#
# Викликається руками (`make pin-images`), КОЛИ ти свідомо оновлюєш базовий
# образ. Не в CI: автоматичний перепін дайджестів щоночі означав би рівно те,
# від чого ми тікаємо, — «на проді приїхало щось нове, і ніхто не знає що».
#
# Результат треба ЗАКОМІТИТИ: `git diff infra/images.env` показує, що саме
# змінилось, а `git revert` повертає як було.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGES_ENV="${REPO_ROOT}/infra/images.env"

# Теги, які ми свідомо тримаємо. Мінор змінюється ТУТ, руками.
declare -A THIRD_PARTY=(
    [REDIS_IMAGE]="redis:7.4-alpine"
)

if [[ ! -f "$IMAGES_ENV" ]]; then
    echo "Немає ${IMAGES_ENV}. Спершу: cp infra/images.env.example infra/images.env" >&2
    exit 1
fi

command -v docker >/dev/null || { echo "Потрібен docker." >&2; exit 1; }

for var in "${!THIRD_PARTY[@]}"; do
    ref="${THIRD_PARTY[$var]}"
    echo "→ ${var}: резолвимо ${ref}…"

    # imagetools питає РЕЄСТР, а не локальний кеш — і віддає дайджест
    # мультиплатформного маніфест-списку (той самий на amd64 і arm64).
    digest="$(docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}')"

    [[ "$digest" == sha256:* ]] || { echo "  не схоже на дайджест: ${digest}" >&2; exit 1; }

    pinned="${ref}@${digest}"
    # BSD sed (macOS) і GNU sed (Linux) по-різному розуміють -i. Через тимчасовий файл — однаково.
    tmp="$(mktemp)"
    sed "s|^${var}=.*|${var}=${pinned}|" "$IMAGES_ENV" > "$tmp"
    mv "$tmp" "$IMAGES_ENV"

    echo "  ${pinned}"
done

echo
echo "Готово. Тепер:"
echo "  git diff infra/images.env      # подивись, що змінилось"
echo "  git add infra/images.env && git commit -m 'infra: перепін базових образів'"
