# 教習所 空き確認

GitHub Actions で毎時、教習生予約サイトにログインして空き時限を確認し、前回なかった枠が出たら ntfy でスマホに通知する。

## セットアップ

1. スマホに ntfy アプリを入れて、推測されにくいトピック名（例: `kyoushuu-x7k2q9`）を購読する
2. GitHub で Private リポジトリを作り、このフォルダの中身をそのまま push する（`.github/workflows/check.yml` も含める）
3. リポジトリの Settings → Secrets and variables → Actions → Secrets に次の4つを登録
   - `FAVORITE_URL` … お気に入り登録用ページのURL（`.../ZADFavo/Favorite.aspx?p=...`）
   - `STUDENT_NO` … 教習生番号
   - `PASSWORD` … 予約サイトのパスワード
   - `NTFY_TOPIC` … 1で決めたトピック名
4. 条件を絞りたいときは同じ画面の Variables に登録（なければ全部通知）
   - `WEEKS` … 予約画面を何週分読むか（デフォルト 2）
   - `DAYS_AHEAD` … 今日から何日先までの空きを通知するか（デフォルト 7）
   - `WEEKDAYS` … 例 `土,日`
   - `PERIODS` … 例 `1-4,8`
5. Actions タブ →「教習所の空き確認」→ Run workflow で一度手動実行して、ログに空き一覧が出るか確認

初回実行では今ある空きが全部「新規」扱いで1回通知される。以降は新しく空いた枠だけ通知。

## 補足

- 1回の実行の中で10分おきに確認を繰り返し（約5時間半）、終わると自分で次の実行を起動する。確認するのは日本時間 6時〜24時（`check.yml` の ACTIVE_FROM / ACTIVE_TO で変更）
- 止めたいときは Actions タブで実行中のものを Cancel するか、ワークフローを Disable する
- 予約対象（AT所内/AT路上など）はサイト側で今の段階が自動で選ばれるので、段階が進んでもそのまま使える
- ログインに失敗し続けたときは最初の1回だけ失敗通知が来る
- 予約そのものはしない（確認と通知だけ）
