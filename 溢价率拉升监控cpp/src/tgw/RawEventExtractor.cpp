#include "tgw/RawEventExtractor.h"

#include <vector>

namespace premium::native_tgw {
namespace {

void skipWhitespace(QByteArrayView input, qsizetype *position)
{
    while (*position < input.size()) {
        const char value = input.at(*position);
        if (value != ' ' && value != '\t' && value != '\r' && value != '\n') break;
        ++*position;
    }
}

bool skipString(QByteArrayView input, qsizetype *position)
{
    if (*position >= input.size() || input.at(*position) != '"') return false;
    ++*position;
    while (*position < input.size()) {
        const char value = input.at(*position);
        ++*position;
        if (value == '"') return true;
        if (value == '\\') {
            if (*position >= input.size()) return false;
            ++*position;
        } else if (static_cast<unsigned char>(value) < 0x20U) {
            return false;
        }
    }
    return false;
}

bool skipValue(QByteArrayView input, qsizetype *position)
{
    skipWhitespace(input, position);
    if (*position >= input.size()) return false;
    if (input.at(*position) == '"') return skipString(input, position);
    if (input.at(*position) == '{' || input.at(*position) == '[') {
        std::vector<char> closing;
        closing.push_back(input.at(*position) == '{' ? '}' : ']');
        ++*position;
        while (*position < input.size() && !closing.empty()) {
            const char value = input.at(*position);
            if (value == '"') {
                if (!skipString(input, position)) return false;
            } else if (value == '{' || value == '[') {
                closing.push_back(value == '{' ? '}' : ']');
                ++*position;
            } else if (value == '}' || value == ']') {
                if (value != closing.back()) return false;
                closing.pop_back();
                ++*position;
            } else {
                ++*position;
            }
        }
        return closing.empty();
    }
    const qsizetype start = *position;
    while (*position < input.size()) {
        const char value = input.at(*position);
        if (value == ',' || value == '}' || value == ']') break;
        ++*position;
    }
    qsizetype end = *position;
    while (end > start) {
        const char value = input.at(end - 1);
        if (value != ' ' && value != '\t' && value != '\r' && value != '\n') break;
        --end;
    }
    return end > start;
}

} // namespace

bool extractRawEvent(QByteArrayView line, ExtractedRawEvent *result, QString *error)
{
    if (error) error->clear();
    if (!result) {
        if (error) *error = QStringLiteral("raw event extraction output is null");
        return false;
    }
    *result = {line, false};
    qsizetype position = 0;
    skipWhitespace(line, &position);
    if (position >= line.size() || line.at(position) != '{') return true;
    ++position;
    for (;;) {
        skipWhitespace(line, &position);
        if (position >= line.size()) return true;
        if (line.at(position) == '}') return true;
        const qsizetype keyStart = position + 1;
        if (!skipString(line, &position)) return true;
        const qsizetype keyEnd = position - 1;
        skipWhitespace(line, &position);
        if (position >= line.size() || line.at(position) != ':') return true;
        ++position;
        skipWhitespace(line, &position);
        const qsizetype valueStart = position;
        if (!skipValue(line, &position)) return true;
        const qsizetype valueEnd = position;
        const QByteArrayView key = line.sliced(keyStart, keyEnd - keyStart);
        if (key == QByteArrayView("event")) {
            if (valueStart >= line.size() || line.at(valueStart) != '{') {
                if (error) *error = QStringLiteral("core raw wrapper event must be an object");
                return false;
            }
            result->payload = line.sliced(valueStart, valueEnd - valueStart);
            result->wrapped = true;
            return true;
        }
        skipWhitespace(line, &position);
        if (position >= line.size() || line.at(position) == '}') return true;
        if (line.at(position) != ',') return true;
        ++position;
    }
}

} // namespace premium::native_tgw
