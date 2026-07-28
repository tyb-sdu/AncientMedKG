import paddle
import paddleocr


def main() -> None:
    print(f"paddle={paddle.__version__}")
    print(f"paddleocr={paddleocr.__version__}")
    print(f"cuda_compiled={paddle.is_compiled_with_cuda()}")
    print(f"cuda_device_count={paddle.device.cuda.device_count()}")
    paddle.utils.run_check()


if __name__ == "__main__":
    main()
