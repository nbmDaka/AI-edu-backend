#include "audio2face/audio2face.h"
#include "audio2x/cuda_utils.h"
#include <nlohmann/json.hpp>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <vector>

using Json = nlohmann::json;
void check(std::error_code error) { if (error) throw std::runtime_error(error.message()); }
struct Destroy { template<class T> void operator()(T* value) const { if (value) value->Destroy(); } };
struct Capture {
    std::mutex mutex;
    Json frames = Json::array();
    std::error_code error;
    std::size_t count = 0;
    double rate = 16000;
};
void receive(void* pointer, const nva2f::IBlendshapeExecutor::HostResults& result, std::error_code error) {
    auto& capture = *static_cast<Capture*>(pointer);
    std::lock_guard<std::mutex> lock(capture.mutex);
    if (error) { capture.error = error; return; }
    if (result.weights.Size() < capture.count) { capture.error = std::make_error_code(std::errc::invalid_argument); return; }
    std::vector<float> weights(result.weights.Data(), result.weights.Data() + capture.count);
    for (float weight : weights) if (!std::isfinite(weight)) { capture.error = std::make_error_code(std::errc::invalid_argument); return; }
    capture.frames.push_back({{"t", double(result.timeStampCurrentFrame) / capture.rate}, {"weights", weights}});
}
void output(const Json& value) { std::cout << "EDUAI_JSON " << value.dump() << std::endl; }
int main(int argc, char** argv) {
    try {
        if (argc != 2) throw std::runtime_error("Expected a model.json path");
        check(nva2x::SetCudaDeviceIfNeeded(0));
        std::unique_ptr<nva2f::IBlendshapeExecutorBundle, Destroy> bundle(
            nva2f::ReadRegressionBlendshapeSolveExecutorBundle(1, argv[1],
                nva2f::IGeometryExecutor::ExecutionOption::Skin, false, 30, 1, nullptr, nullptr));
        if (!bundle) throw std::runtime_error("NVIDIA model initialization failed");
        auto& executor = bundle->GetExecutor();
        nva2f::IBlendshapeSolver* solver = nullptr;
        check(nva2f::GetExecutorSkinSolver(executor, 0, &solver));
        if (!solver) throw std::runtime_error("Skin solver missing");
        Json names = Json::array();
        for (int i = 0; i < solver->NumBlendshapePoses(); ++i) names.push_back(solver->GetPoseName(i));
        Capture capture;
        capture.count = names.size();
        capture.rate = executor.GetSamplingRate();
        check(executor.SetResultsCallback(receive, &capture));
        bool active = false;
        std::size_t received = 0;
        auto reset = [&] {
            check(executor.Wait(0));
            check(executor.Reset(0));
            check(bundle->GetAudioAccumulator(0).Reset());
            check(bundle->GetEmotionAccumulator(0).Reset());
            capture.frames = Json::array();
            capture.error.clear();
            received = 0;
        };
        output({{"ready", true}, {"names", names}, {"sampleRate", capture.rate}});
        std::uint32_t header[2];
        while (std::cin.read(reinterpret_cast<char*>(header), sizeof(header))) {
            const auto operation = header[0], count = header[1];
            if (count > 16000 || (operation != 2 && count != 0)) throw std::runtime_error("Invalid SDK command size");
            if (operation == 1) {
                reset();
                auto& emotions = bundle->GetEmotionAccumulator(0);
                std::vector<float> neutral(emotions.GetEmotionSize(), 0);
                check(emotions.Accumulate(0, nva2x::HostTensorFloatConstView{neutral.data(), neutral.size()}, bundle->GetCudaStream().Data()));
                check(emotions.Close());
                active = true;
            } else if (operation == 2) {
                if (!active || received + count > 16000 * 90) throw std::runtime_error("SDK stream limit exceeded");
                std::vector<float> samples(count);
                if (!std::cin.read(reinterpret_cast<char*>(samples.data()), samples.size() * sizeof(float))) throw std::runtime_error("Truncated SDK input");
                check(bundle->GetAudioAccumulator(0).Accumulate(nva2x::HostTensorFloatConstView{samples.data(), samples.size()}, bundle->GetCudaStream().Data()));
                received += count;
            } else if (operation == 3) {
                if (!active) throw std::runtime_error("SDK stream is not active");
                check(bundle->GetAudioAccumulator(0).Close());
                active = false;
            } else if (operation == 4) {
                reset(); active = false;
            } else throw std::runtime_error("Unknown SDK command");
            while (nva2x::GetNbReadyTracks(executor) > 0) check(executor.Execute(nullptr));
            check(executor.Wait(0));
            check(capture.error);
            output({{"operation", operation}, {"frames", capture.frames}, {"received", received}});
            capture.frames = Json::array();
        }
    } catch (const std::exception& error) {
        output({{"error", error.what()}});
        return 1;
    }
}
