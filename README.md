# Учебный квест и Moodle

## Скачать готовые файлы

- [Квест «Расследование утечки данных»](https://raw.githubusercontent.com/viebanytie/-/refs/heads/education/joyteka-moodle-20261015/downloads/Joyteka_Incident_Quest.zip).
- [Moodle для Windows 10 с готовым курсом](https://raw.githubusercontent.com/viebanytie/-/refs/heads/education/joyteka-moodle-20261015/downloads/Moodle_Windows10_kit.zip).
- [Только курс Moodle (.mbz)](https://raw.githubusercontent.com/viebanytie/-/refs/heads/education/joyteka-moodle-20261015/downloads/Digital_safety_course.mbz).
- [Контрольные суммы](https://raw.githubusercontent.com/viebanytie/-/refs/heads/education/joyteka-moodle-20261015/downloads/SHA256SUMS.txt).

## Квест Joyteka

Учебное дело ML-017, аудитория 16+, 35–45 минут. Пять задач: анализ переписки, сопоставление журналов, оценка подтверждённого объёма утечки, первичное реагирование и обоснованный отчёт. Все данные вымышлены.

Архив содержит досье участника PDF, тексты заданий, ответы, подсказки и инструкцию для конструктора. Для публикации квеста в Joyteka требуется аккаунт и перенос материалов; игровой ссылки пока нет.

## Moodle

Курс «Цифровая безопасность: защити себя и свои данные»: введение, три урока, практика и тест из десяти вопросов. Файл курса успешно восстановлен в отдельную копию Moodle 4.5.15.

Полный архив содержит официальные исходники Moodle, готовый курс, Docker-конфигурацию и файлы запуска Windows. Распакуйте его, запустите совместимый Docker Desktop с WSL2 и откройте `MoodleKit/Start-Moodle.cmd`. На первом запуске задайте собственный пароль администратора. Подробности и вариант установки без Docker — `README_Windows10.txt` внутри архива.

На компьютере пользователя установка не выполнялась. PowerShell-обёртка на Windows отдельно не запускалась; команды Moodle проверены в Linux. Курс не содержит пользователей и их работ. Пароли, рабочие базы и ключи доступа не включены.

Исходники Moodle в полном комплекте сохраняют свои лицензии, включая GNU GPL.
